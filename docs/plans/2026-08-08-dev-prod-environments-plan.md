# Dev/Prod Environments Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run two complete stacks of this app on one Windows machine at the same time — a live `prod` that collects, schedules, notifies and paper-trades, and a quiet `dev` that runs the same services on different ports off a snapshot of prod's data and emits nothing.

**Architecture:** A tracked `config/environments.toml` defines both profiles (port offset, Redis DB, four behavior flags). A gitignored `config/env.local.toml` says which profile *this checkout* is; absent means `prod`, so a `git pull` can never carry an identity between checkouts. `repo_paths.py` resolves the profile at import and derives `SERVICE_PORTS` / `NICEGUI_PORT` / `PROXY_PORT` / `MEMURAI_URL` from it — every consumer already reads those, so they follow the environment for free. Four one-line guards at existing chokepoints turn dev's schedulers, Claude calls, notifications and autonomous trading off by reusing degrade paths the code already has.

**Tech Stack:** Python 3.11 (`tomllib`), pytest, redis-py / Memurai, SQLite online-backup API, Windows batch launchers.

**Design doc:** `docs/plans/2026-08-08-dev-prod-environments-design.md` (commit `c6853db`).

---

## Before you start

**The interpreter.** This work happens in a git worktree that has no `.venv` of its own. Use the main checkout's venv everywhere:

```bash
"D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest tests\test_env_profile.py -v
```

Shorthand below: `$PY` means that full path. In `bash` quote it; in PowerShell use `& "D:\...\python.exe"`.

**Staging discipline.** Another session may be editing this repo concurrently. Never `git add -A` or `git add .` — stage the exact files each task names, then run `git diff --cached --stat` and confirm nothing else came along. This has silently broken a cache key before.

**Two invariants that every task must preserve:**

1. **A checkout with no `config/env.local.toml` behaves exactly as the repo does today.** Every guard defaults to the permissive value. If you ever find yourself writing `if IS_DEV:` to *enable* something, you have it backwards.
2. **Nothing in `dev` may reach out.** No Schwab poll, no Anthropic call, no Discord/Telegram/SMS/X post, no paper trade opened by the driver.

---

## Task 1: Environment profile resolution

The foundation. Everything else reads what this produces.

**Files:**
- Create: `config/environments.toml`
- Modify: `repo_paths.py` (imports at top; new block before the existing `_ports = ...` line)
- Modify: `.gitignore`
- Test: `tests/test_env_profile.py`

**Step 1: Write the failing test**

Create `tests/test_env_profile.py`. The resolver takes an explicit root so tests can point it at a `tmp_path` — `repo_paths` is imported long before any test runs, so reloading the module is not a workable way to test this.

```python
"""Environment-profile resolution (dev/prod) — see
docs/plans/2026-08-08-dev-prod-environments-design.md."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402

PROFILES = """
[prod]
port_offset = 0
redis_db = 0
owns_proxy = true
allow_claude = true
allow_notifications = true
schedulers = true
autonomous_trading = true

[dev]
port_offset = 1000
proxy_port = 8100
redis_db = 1
owns_proxy = false
allow_claude = false
allow_notifications = false
schedulers = false
autonomous_trading = false
"""


def _root(tmp_path, marker=None):
    """A fake checkout root: config/environments.toml always, marker optionally."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "environments.toml").write_text(PROFILES, encoding="utf-8")
    if marker is not None:
        (cfg / "env.local.toml").write_text(marker, encoding="utf-8")
    return tmp_path


def test_missing_marker_resolves_to_prod(tmp_path):
    """The repo's behavior before this feature existed. Fail-safe."""
    name, flags, peer = repo_paths._resolve_env(_root(tmp_path))
    assert name == "prod"
    assert flags["port_offset"] == 0
    assert flags["owns_proxy"] is True
    assert flags["allow_claude"] is True
    assert peer is None


def test_dev_marker_selects_dev_profile(tmp_path):
    root = _root(tmp_path, 'name = "dev"\npeer_root = "D:/WebGUI Trading Prod"\n')
    name, flags, peer = repo_paths._resolve_env(root)
    assert name == "dev"
    assert flags["port_offset"] == 1000
    assert flags["proxy_port"] == 8100
    assert flags["redis_db"] == 1
    assert flags["owns_proxy"] is False
    assert peer == pathlib.Path("D:/WebGUI Trading Prod")


def test_garbage_marker_resolves_to_prod(tmp_path):
    """A truncated or hand-mangled marker must not silently half-apply a profile."""
    name, flags, _ = repo_paths._resolve_env(_root(tmp_path, "name = [broken"))
    assert name == "prod"
    assert flags["allow_notifications"] is True


def test_unknown_env_name_resolves_to_prod(tmp_path):
    name, _, _ = repo_paths._resolve_env(_root(tmp_path, 'name = "staging"\n'))
    assert name == "prod"


def test_missing_profiles_file_still_yields_prod_defaults(tmp_path):
    """No environments.toml at all (e.g. a stale checkout) must not crash import."""
    (tmp_path / "config").mkdir()
    name, flags, _ = repo_paths._resolve_env(tmp_path)
    assert name == "prod"
    assert flags["schedulers"] is True


def test_pytest_forces_suppression_but_keeps_prod_ports(tmp_path):
    """Deliberate: tests are hermetic, so ports are inert constants and the
    existing suites keep passing inside a dev checkout — but no test may ever
    reach Anthropic or a notification channel."""
    _, flags, _ = repo_paths._resolve_env(_root(tmp_path, 'name = "dev"\n'))
    assert flags["port_offset"] == 0        # prod ports under pytest
    assert flags["proxy_port"] is None
    assert flags["allow_claude"] is False
    assert flags["allow_notifications"] is False
    assert flags["schedulers"] is False
    assert flags["autonomous_trading"] is False


def test_live_module_constants_exist():
    """The import-time resolution ran and exported the public names."""
    assert repo_paths.ENV_NAME in ("dev", "prod")
    assert isinstance(repo_paths.ENV_FLAGS, dict)
    assert repo_paths.IS_DEV == (repo_paths.ENV_NAME == "dev")
```

**Step 2: Run it to verify it fails**

```bash
$PY -m pytest tests/test_env_profile.py -v
```

Expected: every test errors with `AttributeError: module 'repo_paths' has no attribute '_resolve_env'`.

**Step 3: Create `config/environments.toml`**

```toml
# Environment profiles — TRACKED, so a profile change is reviewable in git.
#
# WHICH profile a checkout is comes from the GITIGNORED config/env.local.toml
# (name = "dev" | "prod", plus an optional peer_root). A MISSING marker resolves
# to "prod", so any checkout without one behaves exactly as this repo did before
# environments existed. Because the marker is gitignored, `git pull` can never
# carry an environment identity from one checkout into the other.
#
# See docs/plans/2026-08-08-dev-prod-environments-design.md.

[prod]
port_offset        = 0
redis_db           = 0
owns_proxy         = true
allow_claude       = true
allow_notifications = true
schedulers         = true
autonomous_trading = true

[dev]
# Services 9210-9215, webgui 9500. The proxy is NOT offset: dev borrows prod's
# on :8100 rather than holding a second copy of the one rotating Schwab OAuth
# refresh token. Consequence: dev's on-demand fetches need prod's proxy up.
port_offset        = 1000
proxy_port         = 8100
# Same Memurai server, separate logical DB — complete key isolation, and
# FLUSHDB on dev is a safe one-line reset.
redis_db           = 1
owns_proxy         = false
# Dev emits nothing by default. TRADING_ENABLE_SCHEDULERS=1 turns collection on
# for a session when the collectors themselves are what you're testing.
allow_claude       = false
allow_notifications = false
schedulers         = false
autonomous_trading = false
```

**Step 4: Add the resolver to `repo_paths.py`**

Change the import block at the top of the file from:

```python
from pathlib import Path
import tomllib
```

to:

```python
from pathlib import Path
import sys
import tomllib
```

Then insert this block immediately **before** the existing `_ports = tomllib.loads(...)` line near the bottom:

```python
# ------------------------------------------------------------------ environment
# Which environment this CHECKOUT is (dev or prod), and the behavior flags that
# follow from it. Resolution lives here rather than in a new module because this
# file already parses config TOML and is imported by ~40 others — adding a module
# in front of it would create an import-order hazard for no gain.
#
# Design: docs/plans/2026-08-08-dev-prod-environments-design.md

_ENV_DEFAULTS = {
    "port_offset": 0,
    "proxy_port": None,        # None -> port_offset applies to ports.toml's proxy
    "redis_db": 0,
    "owns_proxy": True,
    "allow_claude": True,
    "allow_notifications": True,
    "schedulers": True,
    "autonomous_trading": True,
}


def _read_env_marker(root):
    """``(name, peer_root)`` from the gitignored ``config/env.local.toml``.

    NEVER raises. A missing, unreadable or malformed marker resolves to
    ``("prod", None)`` — the behavior this repo had before environments existed.
    Failing safe matters more than reporting the error: a half-applied profile on
    a live trading stack is worse than no profile.
    """
    try:
        raw = tomllib.loads((root / "config" / "env.local.toml").read_text())
    except Exception:  # noqa: BLE001 — missing file, bad TOML, permissions.
        return "prod", None
    if not isinstance(raw, dict):
        return "prod", None
    name = str(raw.get("name") or "prod").strip().lower() or "prod"
    peer = raw.get("peer_root") or None
    return name, (Path(str(peer)) if peer else None)


def _resolve_env(root):
    """``(name, flags, peer_root)`` for a checkout root. Never raises.

    Takes ``root`` explicitly so it is unit-testable against a tmp_path — this
    module is imported long before any test runs, so reloading it is not a
    workable alternative.
    """
    name, peer = _read_env_marker(root)
    try:
        profiles = tomllib.loads((root / "config" / "environments.toml").read_text())
    except Exception:  # noqa: BLE001
        profiles = {}
    if not isinstance(profiles, dict) or name not in profiles:
        name = "prod"
    flags = dict(_ENV_DEFAULTS)
    over = profiles.get(name)
    if isinstance(over, dict):
        flags.update(over)
    # Under pytest: PROD PORTS regardless of the marker (tests are hermetic — the
    # bus is already fakeredis — so ports are inert constants, and the existing
    # suites keep passing unchanged inside a dev checkout), but every suppression
    # forced ON so no test can reach Anthropic or a notification channel.
    if "pytest" in sys.modules:
        flags["port_offset"] = 0
        flags["proxy_port"] = None
        flags["allow_claude"] = False
        flags["allow_notifications"] = False
        flags["schedulers"] = False
        flags["autonomous_trading"] = False
    return name, flags, peer


ENV_NAME, ENV_FLAGS, PEER_ROOT = _resolve_env(REPO_ROOT)
IS_DEV = ENV_NAME == "dev"
OWNS_PROXY = bool(ENV_FLAGS.get("owns_proxy", True))
```

**Step 5: Gitignore the marker**

In `.gitignore`, under the `# Secrets — NEVER commit` block (it is not a secret, but it is the same class of never-travels-between-checkouts file), add:

```
# Which environment this checkout is (dev|prod). Gitignored ON PURPOSE: a git
# pull must never be able to carry an environment identity between checkouts.
# Absent = prod. See docs/plans/2026-08-08-dev-prod-environments-design.md.
config/env.local.toml
```

**Step 6: Run the tests**

```bash
$PY -m pytest tests/test_env_profile.py -v
```

Expected: 7 passed.

**Step 7: Commit**

```bash
git add config/environments.toml repo_paths.py .gitignore tests/test_env_profile.py
git diff --cached --stat
git commit -m "feat(env): resolve dev/prod profile from a gitignored checkout marker"
```

---

## Task 2: Derive ports and the Redis DB from the profile

**Files:**
- Modify: `repo_paths.py` (the constants block at the bottom)
- Test: `tests/test_env_profile.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_env_profile.py`:

```python
def test_port_derivation_prod(tmp_path):
    """Prod must be byte-identical to the pre-environment numbers."""
    ports = {"proxy": 8100, "nicegui": 8500, "memurai": 6379,
             "services": {"options": 8211, "market": 8215}}
    d = repo_paths._derive_ports(ports, {"port_offset": 0, "proxy_port": None,
                                         "redis_db": 0})
    assert d["proxy_port"] == 8100
    assert d["nicegui_port"] == 8500
    assert d["service_ports"] == {"options": 8211, "market": 8215}
    assert d["memurai_url"] == "redis://127.0.0.1:6379/0"


def test_port_derivation_dev_offsets_and_borrows_proxy(tmp_path):
    ports = {"proxy": 8100, "nicegui": 8500, "memurai": 6379,
             "services": {"options": 8211, "market": 8215}}
    d = repo_paths._derive_ports(ports, {"port_offset": 1000, "proxy_port": 8100,
                                         "redis_db": 1})
    assert d["proxy_port"] == 8100          # borrowed, NOT offset
    assert d["nicegui_port"] == 9500
    assert d["service_ports"] == {"options": 9211, "market": 9215}
    # Memurai PORT is shared (one server); only the logical DB differs.
    assert d["memurai_url"] == "redis://127.0.0.1:6379/1"
```

**Step 2: Run it to verify it fails**

```bash
$PY -m pytest tests/test_env_profile.py -k derivation -v
```

Expected: FAIL, `no attribute '_derive_ports'`.

**Step 3: Implement**

Replace the existing constants block at the bottom of `repo_paths.py`:

```python
_ports = tomllib.loads((REPO_ROOT / "config" / "ports.toml").read_text())
PROXY_PORT       = _ports["proxy"]
...
SERVICE_URLS  = {k: f"http://127.0.0.1:{v}" for k, v in SERVICE_PORTS.items()}
```

with:

```python
_ports = tomllib.loads((REPO_ROOT / "config" / "ports.toml").read_text())


def _derive_ports(ports: dict, flags: dict) -> dict:
    """Apply an environment profile to the base port table. PURE.

    ``port_offset`` shifts the ports this repo OWNS (the six services and the
    webgui). Two things are deliberately left alone:

    * the **Memurai port** — both environments share one Redis server and are
      separated by logical DB index instead, so there is no second service to
      install or monitor;
    * ``options_analytics`` / ``approval`` / the **ML servers** — external
      processes this repo neither starts nor owns.

    ``proxy_port`` overrides the offset entirely, which is how dev borrows prod's
    proxy on :8100 rather than holding a second copy of the one rotating Schwab
    OAuth refresh token.
    """
    off = int(flags.get("port_offset") or 0)
    override = flags.get("proxy_port")
    proxy = int(override) if override else int(ports["proxy"]) + off
    memurai_port = int(ports["memurai"])
    return {
        "proxy_port": proxy,
        "nicegui_port": int(ports["nicegui"]) + off,
        "service_ports": {k: int(v) + off for k, v in ports["services"].items()},
        "memurai_port": memurai_port,
        "memurai_url": f"redis://127.0.0.1:{memurai_port}/{int(flags.get('redis_db') or 0)}",
    }


_derived = _derive_ports(_ports, ENV_FLAGS)

PROXY_PORT       = _derived["proxy_port"]
PROXY_URL        = f"http://127.0.0.1:{PROXY_PORT}"
ANALYTICS_URL    = f"http://127.0.0.1:{_ports['options_analytics']}"
APPROVAL_PORT    = _ports["approval"]
NICEGUI_PORT     = _derived["nicegui_port"]
NICEGUI_URL      = f"http://127.0.0.1:{NICEGUI_PORT}"
ML_SERVER_URLS   = {k: f"http://127.0.0.1:{v}" for k, v in _ports["ml_servers"].items()}
MEMURAI_PORT  = _derived["memurai_port"]
MEMURAI_URL   = _derived["memurai_url"]
SERVICE_PORTS = dict(_derived["service_ports"])
SERVICE_URLS  = {k: f"http://127.0.0.1:{v}" for k, v in SERVICE_PORTS.items()}
```

**Step 4: Run the tests**

```bash
$PY -m pytest tests/test_env_profile.py -v
```

Expected: 9 passed.

**Step 5: Prove nothing regressed for prod**

Every service and the webgui read these constants. Run the full suites — this is the moment a mistake here shows up:

```bash
$PY -m pytest services/options_svc -q
$PY -m pytest services/driver_svc services/market_svc services/sentiment_svc -q
cd webgui && "D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest -q
```

Expected: the documented baselines — options_svc ~916 passing plus the 2 known `test_expected_move` date-relative failures; webgui ~1049 green. Any *new* failure is yours.

**Step 6: Commit**

```bash
git add repo_paths.py tests/test_env_profile.py
git diff --cached --stat
git commit -m "feat(env): derive service/webgui ports and the Redis DB from the profile"
```

---

## Task 3: Suppress notifications in dev

One guard covers all channels: `push_notify.load_config` is a thin wrapper that delegates to `shared.notify.channels.load_config`.

**Files:**
- Modify: `shared/notify/channels.py` (imports; end of `load_config`)
- Test: `shared/notify/tests/test_channels.py` (append — confirm the exact filename with `ls shared/notify/tests`)

**Step 1: Write the failing test**

The strong assertion is not "master `enabled` is False" but "**no** `enabled` key anywhere in the config is True". The X/Twitter poster has its own gate that does not consult the master switch, so a naive guard would leave the one channel that *publishes* still live.

```python
def test_env_suppression_disables_every_channel(tmp_path, monkeypatch):
    """In a suppressed environment NO channel may be enabled — including
    twitter, whose gate is independent of the master `enabled` switch and which
    posts PUBLICLY. Walks the whole config so a channel added later is covered."""
    cfg_file = tmp_path / "notifications.json"
    cfg_file.write_text(json.dumps({
        "enabled": True,
        "telegram": {"bot_token": "t", "chat_id": 1},
        "discord": {"webhook_url": "https://example.invalid/hook"},
        "twitter": {"enabled": True, "dry_run": False},
        "market_snapshot": {"enabled": True},
    }), encoding="utf-8")
    monkeypatch.setitem(channels.ENV_FLAGS, "allow_notifications", False)

    cfg = channels.load_config(cfg_file)

    def enabled_flags(node):
        for k, v in (node or {}).items():
            if k == "enabled":
                yield v
            elif isinstance(v, dict):
                yield from enabled_flags(v)

    assert list(enabled_flags(cfg)), "sanity: the walk found no enabled keys"
    assert not any(enabled_flags(cfg))


def test_env_permissive_leaves_config_untouched(tmp_path, monkeypatch):
    """Prod (and any checkout without a marker) is unaffected."""
    cfg_file = tmp_path / "notifications.json"
    cfg_file.write_text(json.dumps({"enabled": True,
                                    "twitter": {"enabled": True}}), encoding="utf-8")
    monkeypatch.setitem(channels.ENV_FLAGS, "allow_notifications", True)
    cfg = channels.load_config(cfg_file)
    assert cfg["enabled"] is True
    assert cfg["twitter"]["enabled"] is True
```

Add `import json` to the test file if it isn't already imported.

**Step 2: Run it to verify it fails**

```bash
$PY -m pytest shared/notify -k env_ -v
```

Expected: FAIL — `channels` has no attribute `ENV_FLAGS` (and, once that is added, the twitter flag stays True).

**Step 3: Implement**

In `shared/notify/channels.py`, extend the existing repo_paths import (line ~23):

```python
from repo_paths import ENV_FLAGS, NOTIFICATIONS_CONFIG
```

Then at the **end** of `load_config`, immediately before the `return cfg`:

```python
    # Environment gate — a non-prod checkout never talks to a real channel.
    # Every `enabled` flag is zeroed, not just the master one: the X/Twitter
    # poster has its OWN gate that does not consult the master switch, and it is
    # the one channel that PUBLISHES. Recursive so a channel added later is
    # covered without anyone remembering to come back here.
    if not ENV_FLAGS.get("allow_notifications", True):
        def _disable(node):
            for k, v in node.items():
                if k == "enabled":
                    node[k] = False
                elif isinstance(v, dict):
                    _disable(v)
        _disable(cfg)
```

**Step 4: Run the tests**

```bash
$PY -m pytest shared/notify -q
$PY -m pytest services/options_svc/tests/test_push_notify.py -q
```

Expected: all green (the ~56 shared/notify and ~120 push_notify tests). Note that under pytest the flag is already forced False by Task 1, so this also means **no test can send a real notification** — verify a few existing send-path tests still pass, since they monkeypatch config directly and must be unaffected.

**Step 5: Commit**

```bash
git add shared/notify/channels.py shared/notify/tests/test_channels.py
git diff --cached --stat
git commit -m "feat(env): disable every notification channel in a suppressed environment"
```

---

## Task 4: Suppress Claude API calls in dev

Three client factories. Each returns `None`, which drops into the **existing** no-API-key path — the briefing renders its explanatory page, the market ticker's narrative is empty, the driver stands down. No new behavior.

**Files:**
- Modify: `services/options_svc/compute.py` (`_make_analyze_client`, ~line 3455)
- Modify: `services/market_svc/compute.py` (`_make_summary_client`, ~line 340)
- Modify: `services/driver_svc/decider.py` (`_make_client`, ~line 219)
- Test: `tests/test_env_guards.py` (new — a cross-service guard file at the repo root)

**Step 1: Write the failing test**

```python
"""Cross-service environment guards. These are the tests that stop a dev
checkout from spending money or reaching a live channel."""
import importlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

FACTORIES = [
    ("services.options_svc.compute", "_make_analyze_client"),
    ("services.market_svc.compute", "_make_summary_client"),
    ("services.driver_svc.decider", "_make_client"),
]


@pytest.mark.parametrize("mod_name,fn_name", FACTORIES)
def test_claude_factory_returns_none_when_suppressed(mod_name, fn_name, monkeypatch):
    """A suppressed environment builds no Anthropic client, even with a real key
    present on the box — this machine HAS shared/anthropic_key.txt."""
    mod = importlib.import_module(mod_name)
    monkeypatch.setitem(mod.ENV_FLAGS, "allow_claude", False)
    assert getattr(mod, fn_name)() is None
```

> If importing all three services in one process trips the documented top-level
> module-name collision (`config` / `scoring` / `src`), split this into three
> per-service test files under each service's own `tests/` directory instead. Try
> the single file first; the collision is real but these three modules may not
> trigger it.

**Step 2: Run it to verify it fails**

```bash
$PY -m pytest tests/test_env_guards.py -v
```

Expected: FAIL — the modules have no `ENV_FLAGS` attribute.

**Step 3: Implement, one factory at a time**

`services/options_svc/compute.py` — add `ENV_FLAGS` to its existing `repo_paths` import, then as the first line of `_make_analyze_client`:

```python
def _make_analyze_client():
    """A real ``anthropic.Anthropic`` client, or ``None`` if no key / SDK (never
    raises). LAZY import so the test suite + service import without the SDK.

    Returns None in a suppressed environment (dev), which drops into the SAME
    no-API-key path the caller already handles — the briefing renders its
    explanatory page rather than taking any new code path."""
    if not ENV_FLAGS.get("allow_claude", True):
        return None
    key = _anthropic_api_key()
    ...
```

`services/market_svc/compute.py` — same shape in `_make_summary_client`.

`services/driver_svc/decider.py` — same shape in `_make_client`; the caller already treats `None` as stand-down. Import `ENV_FLAGS` from `repo_paths` alongside whatever that module already pulls.

**Step 4: Run the tests**

```bash
$PY -m pytest tests/test_env_guards.py -v
$PY -m pytest services/options_svc -q
$PY -m pytest services/market_svc services/driver_svc -q
```

Expected: 3 new passed; suites at their documented baselines. Watch `services/options_svc/tests/test_compute.py:3646` — it has a comment about the dev box having a real `anthropic_key.txt`; if it asserts a client *is* built, it now needs `monkeypatch.setitem(compute.ENV_FLAGS, "allow_claude", True)`.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/market_svc/compute.py \
        services/driver_svc/decider.py tests/test_env_guards.py
git diff --cached --stat
git commit -m "feat(env): build no Anthropic client in a suppressed environment"
```

---

## Task 5: Suppress schedulers in dev

One guard in the shared scaffold covers all six services.

**Files:**
- Modify: `services/_scaffold.py` (`make_app`, ~line 283; imports)
- Test: `tests/test_env_guards.py` (append)

**Step 1: Write the failing test**

```python
def test_schedulers_disabled_when_suppressed(monkeypatch):
    from services import _scaffold
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", False)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)
    assert _scaffold._schedulers_enabled() is False


def test_env_var_overrides_suppression(monkeypatch):
    """The one dev case that needs collection: testing the collectors."""
    from services import _scaffold
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", False)
    monkeypatch.setenv("TRADING_ENABLE_SCHEDULERS", "1")
    assert _scaffold._schedulers_enabled() is True


def test_health_reports_no_scheduler_rather_than_a_dead_one(monkeypatch):
    """A suppressed service must report `has_scheduler: false`, not a scheduler
    that looks crashed — otherwise the Status page shows dev as broken."""
    from services import _scaffold
    monkeypatch.setitem(_scaffold.ENV_FLAGS, "schedulers", False)
    monkeypatch.delenv("TRADING_ENABLE_SCHEDULERS", raising=False)
    app = _scaffold.make_app("options", scheduler=lambda bus: None)
    with __import__("fastapi").testclient.TestClient(app) as c:
        body = c.get("/health").json()
    assert body["scheduler_alive"] is True   # "not applicable", not "dead"
```

**Step 2: Run it to verify it fails**

```bash
$PY -m pytest tests/test_env_guards.py -k schedul -v
```

Expected: FAIL — `_scaffold` has no `ENV_FLAGS` / `_schedulers_enabled`.

**Step 3: Implement**

In `services/_scaffold.py`, add `from repo_paths import ENV_FLAGS` to the imports (and `import os` if absent), then add above `make_app`:

```python
def _schedulers_enabled() -> bool:
    """Whether this process should run its scheduler.

    False in a suppressed environment (dev), where the stack works off a
    snapshot and must issue no Schwab calls at rest. ``TRADING_ENABLE_SCHEDULERS=1``
    is the escape hatch for the one dev case that genuinely needs collection:
    testing the collectors themselves.
    """
    if os.environ.get("TRADING_ENABLE_SCHEDULERS", "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(ENV_FLAGS.get("schedulers", True))
```

Inside `make_app`, replace:

```python
    health_state = _SchedulerHealth(has_scheduler=scheduler is not None)
```

with:

```python
    # A suppressed environment reports "no scheduler", NOT "scheduler dead" —
    # otherwise /health and the Status page would show a healthy dev stack as
    # broken every time you looked at it.
    run_scheduler = scheduler is not None and _schedulers_enabled()
    health_state = _SchedulerHealth(has_scheduler=run_scheduler)
```

and in the lifespan, change `if scheduler is not None:` to `if run_scheduler:`.

**Step 4: Run the tests**

```bash
$PY -m pytest tests/test_env_guards.py -v
$PY -m pytest services/options_svc/tests/test_app.py services/driver_svc -q
```

Expected: green. If any existing scaffold test asserts a scheduler runs, it must now set `TRADING_ENABLE_SCHEDULERS` or patch `ENV_FLAGS` — under pytest the flag is forced False by Task 1.

**Step 5: Commit**

```bash
git add services/_scaffold.py tests/test_env_guards.py
git diff --cached --stat
git commit -m "feat(env): skip service schedulers in a suppressed environment"
```

---

## Task 6: Hard-disable the autonomous driver in dev

Belt and braces. `cycle` is also a *command*, so the Task 5 scheduler skip alone would not stop a snapshot that arrived carrying `cache:driver:control` enabled.

**Files:**
- Modify: `services/driver_svc/handlers.py` (`run_autonomous_cycle`, line 245)
- Test: `services/driver_svc/tests/test_handlers.py` (append)

**Step 1: Write the failing test**

```python
def test_autonomous_cycle_is_inert_when_suppressed(monkeypatch):
    """A snapshot can carry an ENABLED control key. In dev that must still do
    nothing — no market fetch, no decider call, no command enqueued."""
    from services.driver_svc import handlers
    bus = FakeBus()                                  # the module's existing fake
    handlers.set_control(bus, enabled=True, halted=False)
    monkeypatch.setitem(handlers.ENV_FLAGS, "autonomous_trading", False)
    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda *a, **k: pytest.fail("dev must not fetch market data"))

    handlers.run_autonomous_cycle(bus)

    assert bus.enqueued == []
```

Match `FakeBus` / `bus.enqueued` to whatever that test module already uses.

**Step 2: Run it to verify it fails**

```bash
$PY -m pytest services/driver_svc/tests/test_handlers.py -k inert -v
```

Expected: FAIL — either `handlers` has no `ENV_FLAGS`, or the `fetch_market_context` fail-fast fires.

**Step 3: Implement**

Add `ENV_FLAGS` to `handlers.py`'s `repo_paths` import, then make it the **first** statement of `run_autonomous_cycle`, before `read_control`:

```python
def run_autonomous_cycle(bus) -> None:
    """... (keep the existing docstring, then add:)

    Hard-disabled in a suppressed environment (dev). This is deliberately
    redundant with the scheduler skip: ``cycle`` is also a COMMAND, so a snapshot
    that arrived carrying an enabled ``cache:driver:control`` could otherwise
    start a trading loop in dev.
    """
    if not ENV_FLAGS.get("autonomous_trading", True):
        return
    control = read_control(bus)
    ...
```

**Step 4: Run the tests**

```bash
$PY -m pytest services/driver_svc -q
```

Expected: the documented baseline (~218) plus 1.

**Step 5: Commit**

```bash
git add services/driver_svc/handlers.py services/driver_svc/tests/test_handlers.py
git diff --cached --stat
git commit -m "feat(env): hard-disable the autonomous driver in a suppressed environment"
```

---

## Task 7: Stop dev's shutdown from killing prod

Two real hazards, both already latent in `tools/stop_all.py`.

**Files:**
- Modify: `tools/stop_all.py` (`_targets`, `_is_hud`, `hud_root_pids`)
- Test: `tools/tests/test_stop_all.py` (append — confirm the path with `ls tools/tests`)

**Step 1: Write the failing test**

```python
def test_targets_skip_the_proxy_when_not_owned(monkeypatch):
    """Dev's PROXY_PORT is 8100 — PROD'S. Terminate in dev must not reach it."""
    import tools.stop_all as sa
    monkeypatch.setattr(sa, "OWNS_PROXY", False)
    assert "proxy" not in [label for label, _ in sa._targets()]


def test_targets_include_the_proxy_when_owned(monkeypatch):
    import tools.stop_all as sa
    monkeypatch.setattr(sa, "OWNS_PROXY", True)
    assert sa._targets()[0][0] == "proxy"


def test_hud_match_is_scoped_to_this_checkout():
    """The HUD binds no port, so it is matched by command line — and that match
    cannot tell two checkouts apart. Prod's HUD must survive dev's stop_all."""
    import tools.stop_all as sa
    mine = r"D:\WebGUI Trading Dev"
    theirs = r"D:\WebGUI Trading Prod"
    assert sa._is_hud("pythonw.exe", rf"{mine}\tools\nq_hud.py", root=mine)
    assert not sa._is_hud("pythonw.exe", rf"{theirs}\tools\nq_hud.py", root=mine)


def test_hud_match_unscoped_keeps_legacy_behavior():
    """root=None is the pre-environment behavior, so old callers are unaffected."""
    import tools.stop_all as sa
    assert sa._is_hud("pythonw.exe", r"C:\anywhere\tools\nq_hud.py", root=None)


def test_hud_match_still_rejects_non_python_images():
    """The other load-bearing half of the match: don't kill the shell you typed
    the script name into."""
    import tools.stop_all as sa
    mine = r"D:\WebGUI Trading Dev"
    assert not sa._is_hud("powershell.exe", rf"{mine}\tools\nq_hud.py", root=mine)
```

**Step 2: Run it to verify it fails**

```bash
$PY -m pytest tools/tests/test_stop_all.py -k "proxy or scoped or legacy" -v
```

Expected: FAIL — `OWNS_PROXY` missing and `_is_hud` takes no `root`.

**Step 3: Implement**

In `tools/stop_all.py`, extend the repo_paths import:

```python
from repo_paths import NICEGUI_PORT, OWNS_PROXY, PROXY_PORT, REPO_ROOT, SERVICE_PORTS  # noqa: E402
```

Rewrite `_targets`:

```python
def _targets():
    """Ordered (label, port) list — proxy + services first, web GUI LAST.

    The proxy is included ONLY when this environment owns it. In dev, PROXY_PORT
    is 8100 — PROD'S proxy, borrowed — so including it here would make dev's
    Terminate button take the live stack's market data down.
    """
    targets = [("proxy", PROXY_PORT)] if OWNS_PROXY else []
    targets += [(f"{name}_svc", port) for name, port in SERVICE_PORTS.items()]
    targets.append(("webgui", NICEGUI_PORT))
    return targets
```

Add the root scope to `_is_hud` — keep both existing conditions and their docstring, adding a third:

```python
def _is_hud(name, cmdline, root=None):
    """True when a process row is the Dealer-Positioning HUD.

    THREE conditions now, and all are load-bearing:

    * the command line runs HUD_SCRIPT ... (keep existing text)
    * the image is a python interpreter ... (keep existing text)
    * when ``root`` is given, the command line names THIS checkout. The HUD binds
      no port, so unlike every other target it cannot be attributed by port — and
      with two checkouts on one machine, an unscoped match makes dev's stop_all
      kill prod's HUD. ``root=None`` keeps the pre-environment behavior.
    """
    if not name or not cmdline:
        return False
    if not str(name).lower().startswith("python"):
        return False
    cl = str(cmdline).lower()
    if HUD_SCRIPT not in cl:
        return False
    if root is not None and str(root).lower().replace("/", "\\") not in cl.replace("/", "\\"):
        return False
    return True
```

Then in `hud_root_pids`, add a `root=None` parameter, pass it through to `_is_hud`, and at the call site pass `root=REPO_ROOT`.

**Step 4: Run the tests**

```bash
$PY -m pytest tools/tests/test_stop_all.py -v
```

Expected: all green, including the pre-existing HUD-matching tests (they call `_is_hud` with two args, which still works).

**Step 5: Commit**

```bash
git add tools/stop_all.py tools/tests/test_stop_all.py
git diff --cached --stat
git commit -m "fix(env): stop_all must not reach another checkout's proxy or HUD"
```

---

## Task 8: Status page — don't offer restarts you don't own

**Files:**
- Modify: `webgui/pages/status.py` (`component_targets` line 71, `restart_spec` line 225)
- Test: `webgui/tests/test_status.py` (append)

**Step 1: Write the failing test**

```python
def test_proxy_restart_is_withheld_when_not_owned(monkeypatch):
    """In dev the proxy card would otherwise offer a button that bounces PROD's
    proxy — the live stack's market data."""
    monkeypatch.setattr(status, "OWNS_PROXY", False)
    proxy = [t for t in status.component_targets() if t["key"] == "proxy"][0]
    assert proxy["owned"] is False
    assert "shared" in proxy["label"].lower()
    assert status.restart_spec(proxy) is None


def test_proxy_restart_offered_when_owned(monkeypatch):
    monkeypatch.setattr(status, "OWNS_PROXY", True)
    proxy = [t for t in status.component_targets() if t["key"] == "proxy"][0]
    assert status.restart_spec(proxy)["kind"] == "script"


def test_memurai_restart_withheld_in_dev(monkeypatch):
    """One Memurai serves both environments — restarting it from dev would take
    prod's Redis down with it."""
    monkeypatch.setattr(status, "IS_DEV", True)
    mem = [t for t in status.component_targets() if t["key"] == "memurai"][0]
    assert status.restart_spec(mem) is None


def test_services_and_webgui_stay_restartable_in_dev(monkeypatch):
    """Dev owns its own six services and its own webgui."""
    monkeypatch.setattr(status, "IS_DEV", True)
    monkeypatch.setattr(status, "OWNS_PROXY", False)
    for t in status.component_targets():
        if t["kind"] in ("service", "self"):
            assert status.restart_spec(t) is not None
```

**Step 2: Run it to verify it fails**

```bash
cd webgui && "D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest tests/test_status.py -k "owned or memurai" -v
```

Expected: FAIL — no `OWNS_PROXY` / `IS_DEV` on the module, no `owned` key.

**Step 3: Implement**

Add to `webgui/pages/status.py`'s repo_paths import: `IS_DEV, OWNS_PROXY`.

In `component_targets`, replace the proxy entry:

```python
        {"key": "proxy",
         "label": ("schwab-proxy (market data / auth)" if OWNS_PROXY
                   else "schwab-proxy (shared — owned by prod)"),
         "tier": "Tier 1", "kind": "proxy", "url": PROXY_URL,
         "owned": OWNS_PROXY},
```

In `restart_spec`, extend the docstring's bullet list and guard both branches:

```python
    if kind == "proxy":
        # Not ours to restart when borrowed: in dev this port is PROD's proxy.
        if not OWNS_PROXY:
            return None
        return {"kind": "script", ...}
```

```python
    if kind == "memurai":
        # One Memurai serves both environments (separated by logical DB), so a
        # restart from dev would take prod's Redis down with it.
        if IS_DEV:
            return None
        return {"kind": "service", "title": "Memurai", "service": "Memurai"}
```

**Step 4: Run the tests**

```bash
cd webgui && "D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest tests/test_status.py -q
```

Expected: the ~35 status tests plus 4.

**Step 5: Commit**

```bash
git add webgui/pages/status.py webgui/tests/test_status.py
git diff --cached --stat
git commit -m "feat(env): withhold Status-page restarts for components this env doesn't own"
```

---

## Task 9: Make dev's browser tab unmistakable

Two identical-looking NeuralStrike tabs writing to different paper books is a mistake waiting to happen.

**Files:**
- Modify: `webgui/main.py` (`brand_lockup_html` line 392; `ui.run` line 1422)
- Test: `webgui/tests/test_shell.py` (append)

**Step 1: Write the failing test**

```python
def test_brand_lockup_carries_a_dev_chip(monkeypatch):
    monkeypatch.setattr(main, "IS_DEV", True)
    assert "DEV" in main.brand_lockup_html()


def test_brand_lockup_is_unchanged_in_prod(monkeypatch):
    monkeypatch.setattr(main, "IS_DEV", False)
    assert "DEV" not in main.brand_lockup_html()


def test_window_title_is_prefixed_in_dev(monkeypatch):
    monkeypatch.setattr(main, "IS_DEV", True)
    assert main.window_title().startswith("DEV")


def test_window_title_is_the_brand_in_prod(monkeypatch):
    monkeypatch.setattr(main, "IS_DEV", False)
    assert main.window_title() == theme.BRAND_NAME
```

**Step 2: Run it to verify it fails**

```bash
cd webgui && "D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest tests/test_shell.py -k "dev_chip or window_title" -v
```

Expected: FAIL.

**Step 3: Implement**

Add `IS_DEV` to `main.py`'s repo_paths import. In `brand_lockup_html`, before the return:

```python
    # A DEV chip, because two environments mean two identical-looking tabs and
    # they write to different paper books. Raw HTML like the rest of the lockup.
    chip = ('<span style="margin-left:8px;padding:1px 7px;border-radius:4px;'
            'background:#b45309;color:#fff;font-size:10px;font-weight:700;'
            'letter-spacing:.06em">DEV</span>') if IS_DEV else ""
```

and append `{chip}` inside the outer `<div>`, after the wordmark `</span>`.

Add beside it:

```python
def window_title():
    """Browser tab title — env-prefixed in dev so two tabs are tellable apart."""
    return f"DEV · {theme.BRAND_NAME}" if IS_DEV else theme.BRAND_NAME
```

and change `ui.run(..., title=theme.BRAND_NAME, ...)` to `title=window_title()`.

**Step 4: Run the tests**

```bash
cd webgui && "D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest -q
```

Expected: the ~1049 baseline plus 4.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git diff --cached --stat
git commit -m "feat(env): mark the dev webgui with a DEV chip and tab-title prefix"
```

---

## Task 10: `start_dev.bat`

A **separate** launcher rather than an env-aware `start_all_wt.bat`. Prod's launcher is load-bearing for a stack that has to come up every morning; the design's rule is that prod's behavior is unchanged, and the batch files only need real port numbers for their *tab titles* (the services read ports from `repo_paths` themselves).

**Files:**
- Create: `start_dev.bat`

**Step 1: Write it**

Copy `start_all_wt.bat` to `start_dev.bat` and make exactly four changes:

1. Title/banner say `NeuralStrike DEV`, and list `9210-9215` / `9500`.
2. **Delete the proxy tab.** Dev borrows prod's.
3. Every remaining tab keeps `call tools\wait_and_run.bat 8100 ...` — waiting on *prod's* proxy is correct and gives a clear failure when prod is down.
4. Add a guard before launching:

```bat
REM --- Refuse to run unless this checkout is marked dev. Launching the dev stack
REM     from the prod checkout would bind prod's ports twice and fail confusingly.
"%PY%" -c "import sys,repo_paths; sys.exit(0 if repo_paths.IS_DEV else 1)"
if errorlevel 1 (
    echo This checkout is not marked as dev.
    echo Create config\env.local.toml containing:  name = "dev"
    pause
    exit /b 1
)
```

Also update the browser-open helper to use the dev webgui port.

**Step 2: Verify the guard fires**

From this (unmarked, therefore prod) checkout:

```bash
cmd /c start_dev.bat
```

Expected: it refuses with the `not marked as dev` message and starts nothing.

**Step 3: Commit**

```bash
git add start_dev.bat
git diff --cached --stat
git commit -m "feat(env): start_dev.bat — dev stack without a proxy, guarded on the marker"
```

---

## Task 11: The snapshot tool

**Files:**
- Create: `tools/snapshot_from_prod.py`
- Test: `tools/tests/test_snapshot_from_prod.py`

**Step 1: Write the failing tests**

The planner is pure, so the part that decides *what gets copied* is testable without touching a real database.

```python
"""Snapshot prod -> dev. The planner is pure; the copies are smoke-tested."""
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402
from tools import snapshot_from_prod as snap  # noqa: E402


def test_plan_maps_every_store_across_roots(tmp_path):
    src, dst = tmp_path / "prod", tmp_path / "dev"
    plan = snap.snapshot_plan(src, dst)
    assert plan, "the plan must not be empty"
    for item in plan:
        assert item.src.is_relative_to(src)
        assert item.dst.is_relative_to(dst)
        assert item.src.relative_to(src) == item.dst.relative_to(dst)


def test_plan_includes_the_gex_history_store(tmp_path):
    rels = {i.src.relative_to(tmp_path / "prod").as_posix()
            for i in snap.snapshot_plan(tmp_path / "prod", tmp_path / "dev")}
    assert "options-scanner/gex_history.db" in rels
    assert "options-scanner/data/paper_account.db" in rels
    assert "shared/sentiment_bridge.json" in rels


def test_skip_gex_drops_only_the_big_store(tmp_path):
    full = snap.snapshot_plan(tmp_path / "p", tmp_path / "d")
    lean = snap.snapshot_plan(tmp_path / "p", tmp_path / "d", skip_gex=True)
    dropped = {i.src.name for i in full} - {i.src.name for i in lean}
    assert dropped == {"gex_history.db"}


def test_sqlite_copy_reproduces_rows_while_source_stays_open(tmp_path):
    """The online-backup API is the whole point: prod keeps running."""
    src, dst = tmp_path / "a.db", tmp_path / "b.db"
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    con.commit()
    snap.copy_sqlite(src, dst)          # source connection deliberately left open
    assert sqlite3.connect(dst).execute("SELECT count(*) FROM t").fetchone()[0] == 3
    con.close()


def test_redis_copy_excludes_command_streams_and_disarms_the_driver():
    import fakeredis
    src, dst = fakeredis.FakeStrictRedis(), fakeredis.FakeStrictRedis()
    src.set("cache:options:scan", b"payload")
    src.set("cache:options:scan:ver", b"7")
    src.set("cache:driver:control", b'{"enabled": true, "halted": false}')
    src.xadd("cmd:options", {"a": "1"})
    dst.set("stale:key", b"x")

    snap.copy_redis(src, dst)

    assert dst.get("cache:options:scan") == b"payload"
    assert dst.get("cache:options:scan:ver") == b"7"
    assert not dst.exists("cmd:options"), "a queued command backlog must not follow"
    assert not dst.exists("stale:key"), "the destination DB is flushed first"
    assert b'"enabled": false' in dst.get("cache:driver:control").replace(b" ", b" ")


def test_refuses_to_run_outside_a_dev_checkout(monkeypatch):
    """The one guard that makes this tool safe to keep in the repo."""
    monkeypatch.setattr(snap, "ENV_NAME", "prod")
    with pytest.raises(SystemExit):
        snap.assert_destination_is_dev()
```

**Step 2: Run to verify it fails**

```bash
$PY -m pytest tools/tests/test_snapshot_from_prod.py -v
```

Expected: collection error — the module does not exist.

**Step 3: Implement `tools/snapshot_from_prod.py`**

```python
"""Snapshot prod's live data into the dev checkout.

Run FROM THE DEV CHECKOUT (the destination). Dev's collectors are off by
default, so this is what makes a dev stack show anything at all: it brings over
every published Redis view and every SQLite store, after which dev renders
exactly what prod renders.

PROD KEEPS RUNNING throughout. SQLite is copied with the online-backup API
rather than a file copy, which is consistent under concurrent writers; a plain
copy of a live WAL database is not.

    python tools\\snapshot_from_prod.py [--dry-run] [--redis-only] [--skip-gex]

Design: docs/plans/2026-08-08-dev-prod-environments-design.md
"""
import argparse
import dataclasses
import pathlib
import shutil
import sqlite3
import sys
import time

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import ENV_FLAGS, ENV_NAME, MEMURAI_PORT, PEER_ROOT, REPO_ROOT, SERVICE_PORTS, NICEGUI_PORT  # noqa: E402

# Repo-relative stores, all gitignored, so a fresh checkout has none of them.
GEX_STORE = "options-scanner/gex_history.db"
SQLITE_STORES = [
    GEX_STORE,
    "options-scanner/data/trades.db",
    "options-scanner/data/signals.db",
    "options-scanner/data/paper_account.db",
    "options-scanner/data/paper_account_driver.db",
    "options-scanner/data/gamma_briefings.db",
    "options-scanner/data/daily_trade_log.db",
    "sentiment-dashboard/data/sentiment_intraday.db",
    "sentiment-dashboard/data/sector_pcr.db",
    "sentiment-dashboard/data/momentum.db",
    "sentiment-dashboard/data/market_state.db",
    "services/trade_svc/data/iv_history.db",
]
# Plain files: the scanner watchlist and the sentiment bridge regime_filter reads.
FILE_STORES = [
    "options-scanner/data/Top 20.xlsx",
    "shared/sentiment_bridge.json",
]
REDIS_EXCLUDE_PREFIXES = ("cmd:",)


@dataclasses.dataclass(frozen=True)
class Item:
    src: pathlib.Path
    dst: pathlib.Path
    kind: str          # "sqlite" | "file"


def snapshot_plan(src_root, dst_root, *, skip_gex=False):
    """Every file the snapshot copies, as ``Item``s. PURE — no filesystem access.

    Kept pure and separate from the copying so the decision of WHAT moves is
    unit-testable without a 1.5 GB database on disk.
    """
    src_root, dst_root = pathlib.Path(src_root), pathlib.Path(dst_root)
    plan = []
    for rel in SQLITE_STORES:
        if skip_gex and rel == GEX_STORE:
            continue
        plan.append(Item(src_root / rel, dst_root / rel, "sqlite"))
    for rel in FILE_STORES:
        plan.append(Item(src_root / rel, dst_root / rel, "file"))
    return plan


def assert_destination_is_dev():
    """Exit unless THIS checkout is dev. The tool can never overwrite prod."""
    if ENV_NAME != "dev":
        sys.exit(
            f"refusing to run: this checkout resolves to '{ENV_NAME}', not 'dev'.\n"
            "Run it from the DEV checkout (the destination). Its config/env.local.toml\n"
            'must contain:  name = "dev"'
        )


def assert_dev_stack_is_down():
    """Exit if dev's services are listening — they hold DB handles and would
    write over the restore mid-copy."""
    import socket
    busy = []
    for label, port in list(SERVICE_PORTS.items()) + [("webgui", NICEGUI_PORT)]:
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(f"{label}:{port}")
    if busy:
        sys.exit("refusing to run: dev is still up on " + ", ".join(busy)
                 + "\nStop it first (stop_all.bat), then re-run.")


def copy_sqlite(src, dst):
    """Online backup — safe while the source is being written to."""
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def copy_file(src, dst):
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_redis(src, dst):
    """DUMP/RESTORE every key from prod's DB into dev's, preserving type + TTL.

    Two deliberate exclusions:

    * ``cmd:*`` streams — dev must not inherit a queued command backlog and
      execute yesterday's prod commands on startup;
    * ``cache:driver:control`` is rewritten disabled, so a snapshot taken while
      the autonomous driver was armed cannot arm it in dev.
    """
    dst.flushdb()
    for key in src.scan_iter(match="*", count=500):
        name = key.decode() if isinstance(key, bytes) else str(key)
        if name.startswith(REDIS_EXCLUDE_PREFIXES):
            continue
        payload = src.dump(key)
        if payload is None:                      # expired between scan and dump
            continue
        pttl = src.pttl(key)
        dst.restore(key, pttl if pttl and pttl > 0 else 0, payload, replace=True)
    dst.set("cache:driver:control", b'{"enabled": false, "halted": false}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redis-only", action="store_true")
    ap.add_argument("--skip-gex", action="store_true",
                    help="skip gex_history.db (the ~1.5 GB one)")
    ap.add_argument("--peer", default=None, help="prod checkout root (else peer_root)")
    args = ap.parse_args(argv)

    assert_destination_is_dev()
    peer = pathlib.Path(args.peer) if args.peer else PEER_ROOT
    if not peer or not peer.exists():
        sys.exit("no prod checkout: set peer_root in config/env.local.toml, or pass --peer")
    if not args.dry_run:
        assert_dev_stack_is_down()

    print(f"snapshot  {peer}  ->  {REPO_ROOT}")

    if not args.redis_only:
        for item in snapshot_plan(peer, REPO_ROOT, skip_gex=args.skip_gex):
            if not item.src.exists():
                print(f"  skip  {item.src.name} (absent in prod)")
                continue
            mb = item.src.stat().st_size / 1e6
            if args.dry_run:
                print(f"  would copy  {item.src.name}  {mb:.0f} MB")
                continue
            t0 = time.monotonic()
            (copy_sqlite if item.kind == "sqlite" else copy_file)(item.src, item.dst)
            print(f"  copied  {item.src.name}  {mb:.0f} MB  {time.monotonic()-t0:.1f}s")

    import redis
    src_db = int(_peer_redis_db(peer))
    dst_db = int(ENV_FLAGS.get("redis_db") or 0)
    print(f"  redis db {src_db} -> db {dst_db}"
          + ("  (dry run)" if args.dry_run else ""))
    if not args.dry_run:
        copy_redis(redis.Redis(port=MEMURAI_PORT, db=src_db),
                   redis.Redis(port=MEMURAI_PORT, db=dst_db))
    print("done.")


def _peer_redis_db(peer):
    """Prod's Redis DB index, read from ITS environments.toml + marker (default 0)."""
    import tomllib
    try:
        name = tomllib.loads((peer / "config" / "env.local.toml").read_text()).get("name", "prod")
    except Exception:  # noqa: BLE001
        name = "prod"
    try:
        profiles = tomllib.loads((peer / "config" / "environments.toml").read_text())
        return (profiles.get(name) or {}).get("redis_db", 0)
    except Exception:  # noqa: BLE001
        return 0


if __name__ == "__main__":
    main()
```

**Step 4: Run the tests**

```bash
$PY -m pytest tools/tests/test_snapshot_from_prod.py -v
```

Expected: 7 passed. If `tools/` has no `__init__.py`, import the module by path in the test instead of `from tools import ...`.

**Step 5: Commit**

```bash
git add tools/snapshot_from_prod.py tools/tests/test_snapshot_from_prod.py
git diff --cached --stat
git commit -m "feat(env): snapshot prod's Redis + SQLite stores into the dev checkout"
```

---

## Task 12: `tools/promote.bat`

**Files:**
- Create: `tools/promote.bat`

**Step 1: Write it**

```bat
@echo off
REM Promote main into THIS checkout and restart the stack. Prod-only, on purpose:
REM prod is pinned to main and changes only when you say so.
cd /d "%~dp0.."
set "PY=%CD%\.venv\Scripts\python.exe"

"%PY%" -c "import sys,repo_paths; sys.exit(1 if repo_paths.IS_DEV else 0)"
if errorlevel 1 (
    echo This checkout is marked dev. Promote runs in the PROD checkout.
    pause
    exit /b 1
)

echo Stopping the stack...
call stop_all.bat

echo Pulling main...
git checkout main || (echo could not switch to main & pause & exit /b 1)
git pull --ff-only || (echo pull failed - resolve manually & pause & exit /b 1)

REM Reinstall only when the lockfile actually moved.
git diff --quiet HEAD@{1} HEAD -- requirements.lock
if errorlevel 1 (
    echo requirements.lock changed - reinstalling...
    "%PY%" -m pip install -r requirements.lock
)

echo Restarting...
call start_all_wt.bat nowindow
echo Promoted. Check /status.
```

**Step 2: Verify the guard**

Nothing to test automatically — confirm by reading that the dev guard is inverted relative to `start_dev.bat`'s (this one refuses in dev, that one refuses in prod).

**Step 3: Commit**

```bash
git add tools/promote.bat
git diff --cached --stat
git commit -m "feat(env): promote.bat — pull main and restart, prod checkout only"
```

---

## Task 13: Document the cutover and update CLAUDE.md

**Files:**
- Create: `docs/dev-prod-environments.md`
- Modify: `CLAUDE.md`

**Step 1: Write `docs/dev-prod-environments.md`**

An operator runbook, not a design doc. Cover:

1. **Which folder is which**, and the two files that decide it.
2. **One-time cutover checklist**, in order:
   - `git clone` the repo to `D:\WebGUI Trading Prod`, `git checkout main`
   - `python -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.lock`
   - copy the gitignored secrets from the dev checkout: `shared/appsettings.json`, `shared/tokens.json`, `shared/notifications.json`, `shared/anthropic_key.txt`, `shared/driver_model.txt`, `schwab-proxy/proxy_tokens.json`
   - copy `options-scanner/data/Top 20.xlsx` (without it the scanner degrades to base symbols and the SPDR-sector Net Prem group empties)
   - stop the current stack; copy the live data into prod — the whole `options-scanner/data`, `options-scanner/gex_history.db`, `sentiment-dashboard/data`, `services/trade_svc/data`, `webgui/data`
   - write `config/env.local.toml` in prod: `name = "prod"` (explicit, though absent would also work)
   - write `config/env.local.toml` in dev: `name = "dev"` and `peer_root = "D:/WebGUI Trading Prod"`
   - start prod with `start_all_wt.bat`; check `/status` is all green and Schwab shows authorized
   - repoint the desktop shortcut at prod's `start_all_hidden.bat`
3. **Daily dev loop:** stop dev → `python tools\snapshot_from_prod.py` → `start_dev.bat` → work at `http://127.0.0.1:9500`.
4. **Turning collection on in dev**, and why you rarely should: `set TRADING_ENABLE_SCHEDULERS=1` before launching. It makes dev issue real Schwab calls on top of prod's ~68–76k/day.
5. **Promotion:** merge to `main` and push from dev; run `tools\promote.bat` in prod.
6. **The gotchas**, stated plainly: dev's on-demand fetches need prod's proxy up; restarting Memurai takes both down; dev's Terminate stops only dev; a snapshot never arms dev's driver.

**Step 2: Update `CLAUDE.md`**

Add a `## Environments (dev / prod)` section after "Running", with the topology table, the two config files, the four suppression flags and where each is enforced, and a pointer to the runbook and the design doc. Add a line to the **Last updated** block per the file's own standing convention.

**Step 3: Commit**

```bash
git add docs/dev-prod-environments.md CLAUDE.md
git diff --cached --stat
git commit -m "docs(env): dev/prod runbook + CLAUDE.md environments section"
```

---

## Task 14: Live verification

No code. This is the acceptance gate, and it is the only step that proves the feature.

**Step 1: Stand prod up** from `D:\WebGUI Trading Prod` per the runbook. Confirm `http://127.0.0.1:8500/status` is green across Memurai, proxy, Schwab auth, all six services and the webgui, and that the header carries **no** DEV chip.

**Step 2: Snapshot and start dev.**

```bash
python tools\snapshot_from_prod.py
start_dev.bat
```

**Step 3: Both up at once.** `:8500` and `:9500` both load. Dev's header shows the **DEV** chip and the tab title starts `DEV ·`. Dev's Gamma heatmap, Opportunity Board and Paper Ledger show the snapshot's data — that is the whole point of the snapshot, and an empty page here means the copy silently failed.

**Step 4: The acceptance test — dev at rest is silent.** Read prod's counter, wait 10 minutes with dev idle, read it again:

```bash
curl http://127.0.0.1:8100/stats/api_calls
```

Dev must add **zero** calls. A non-zero delta means a scheduler survived Task 5; find it before going further.

**Step 5: Dev's shutdown doesn't touch prod.** Hit **More → Stop All Services** in dev (`:9500`). Then confirm prod's `:8500` still loads, `/status` is still green, and prod's proxy on `:8100` still answers. This is the hazard from Task 7 — verify it directly rather than trusting the unit test.

**Step 6: Dev emits nothing.** In dev, run a scan and open a Gamma briefing. Expect: no phone notification, no Discord message, and the briefing showing its no-API-key explanatory page rather than a generated one.

**Step 7: Record the result.** Append a `## Verification` section to the design doc with what was actually observed — especially the API-call delta and the snapshot's elapsed time and size. Commit.

```bash
git add docs/plans/2026-08-08-dev-prod-environments-design.md
git commit -m "docs(env): record live dev/prod verification results"
```

---

## Notes for whoever executes this

- **Tasks 1 and 2 are the risky ones.** They change constants that ~40 files import. Run the full suites after Task 2, not just the new tests.
- **Task 3's recursive disable is deliberate.** Zeroing only the master `enabled` would leave the X/Twitter poster live, because its gate is independent. That is the one channel that publishes.
- **The `pytest → prod ports` decision** (Task 1) is what keeps the existing ~3,400 tests passing unchanged inside a dev checkout. If a test starts failing on a port number, that is the thing to re-read before "fixing" the test.
- **Do not add an `if IS_DEV:` that enables anything.** Every guard defaults permissive so that a checkout with no marker is byte-identical to today's repo.
