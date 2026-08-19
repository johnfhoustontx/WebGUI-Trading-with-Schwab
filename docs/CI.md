# CI, linting & dependency hygiene

This repo ships a lightweight, **non-breaking** engineering backstop: a lenient ruff
lint gate, a per-folder test matrix in GitHub Actions, and pinned dependencies. None
of it repackages the monorepo — the apps still run from their hyphenated,
`sys.path`-mounted directories exactly as before.

## Ruff (lint) — run it locally

Config lives in **`pyproject.toml`** under `[tool.ruff]` (consolidated from the former
`ruff.toml` so there is a single ruff config source — two configs make ruff ignore the
`pyproject.toml` one with a warning).

```powershell
.venv\Scripts\python -m ruff check .          # the gate CI runs — must be clean
.venv\Scripts\python -m ruff check . --fix    # auto-fix the safe subset (optional)
```

**Philosophy — lenient, catch real bugs only.** The rule set is the minimal
high-signal correctness subset:

| Rule class | Catches |
|-----------|---------|
| `E9`  | syntax errors |
| `F63` | invalid comparisons / `assert (a, b)` tuple bugs / `is` on literals |
| `F7`  | misplaced statements (`break`/`continue`/`return` outside a loop/function) |
| `F82` | undefined names (typos, missing imports) — the highest-value check |

Deliberately **not** selected: `F401` (unused import), `F811` (redefinition), and all
`E1xx–E7xx` style rules. The codebase uses an intentional compact one-line style
(`x = 1; y = 2`, `if cond: return`) that those rules would flag as noise, and the tree
currently has a benign unused import that `F401` would trip on — so a broader select
would make the gate red without any real defect. `line-length = 120`. `ruff format` is
**not** enforced (it would rewrite the compact style wholesale).

**Scope / excludes.** `.venv`, `**/data`, `**/logs`, `**/frontend`, `**/node_modules`,
`docs`, plus the **grandfathered legacy engine dirs** (`options-scanner`,
`sentiment-dashboard`, `trade-analyzer`, `portfolio-analyzer`, `claude-driver`) and a
few legacy Tk modules under `shared/analysis_lib/` — these were copied verbatim from
the source monorepo and carry pre-existing lint debt; tightening them is a separate pass.

Pre-commit mirrors this gate (`.pre-commit-config.yaml`): `pip install pre-commit &&
pre-commit install`, then `pre-commit run --all-files`.

## Test matrix — mirrors the CLAUDE.md test discipline

The root `CLAUDE.md` "Tests" section is emphatic: **run each suite one folder at a
time.** Running the whole tree in a single `pytest` re-triggers the cross-app top-level
module-name collisions (`config` / `scoring` / `notifier` / `src`) that the 3-tier split
exists to prevent. `.github/workflows/ci.yml` encodes this as **one isolated matrix job
per suite** (`fail-fast: false`, so one red suite doesn't cancel the others):

| Suite | Runs from | Command |
|-------|-----------|---------|
| `shared/bus`, `shared/contracts` | repo root | `python -m pytest shared/bus` (etc.) |
| `services/tests` (scaffold) | repo root | `python -m pytest services/tests` |
| `services/{sentiment,options,portfolio,trade,driver,market}_svc` | repo root | `python -m pytest services/<name>` |
| `webgui` | **inside** `webgui/` | `python -m pytest` |
| `schwab-proxy` | **inside** `schwab-proxy/` | `python -m pytest tests` |
| `options-scanner` *(soft)* | **inside** `options-scanner/` | `python -m pytest tests` (+ deselections) |
| `sentiment-dashboard` *(soft)* | **inside** | `python -m pytest tests` |
| `trade-analyzer` *(soft)* | **inside** | `python -m pytest .` |
| `portfolio-analyzer` *(soft)* | **inside** | `python -m pytest tests` |

The clean **3-tier suites run from the repo root** (their entrypoints add the repo root
to `sys.path`); the **app folders run from inside the folder** (each has its own
`conftest.py` / `sys.path` setup). The copied **legacy engine dirs are `soft`
(`continue-on-error`)** — they surface for review without wedging the clean-stack gate.

**Runner OS: `windows-latest`.** The stack is Windows-first — it imports `winotify`
(Windows-only) and touches tkinter/Memurai — so a Linux runner would fail at import
time. This is intentional, not a portability bug.

**No Redis service container needed.** Under pytest the bus falls back to
**`fakeredis`** (`shared/bus/client.py` → `fakeredis.FakeStrictRedis`), and the Schwab
proxy is mocked, so the suites need no live Memurai/Redis/network.

### Known pre-existing `options-scanner` failures (do not "fix" as part of other work)

`options-scanner` was copied verbatim and carries a small documented baseline of
failures, so its job is `soft` and the following are deselected:

- `tests/test_dashboard_captured_signals_drift.py` — tkinter dashboard test; needs a
  display and crashes headless.
- `tests/test_dashboard_tree_signal_map.py` — same tkinter-display class.
- `tests/test_key_levels_doc.py` — references a doc that was not copied into this repo.
- `tests/test_scanner_engine.py::TestEarningsAvoidance` — date-relative (asserts against
  "now").

## Dependency pinning & lockfile

- **`requirements.txt`** — the human-readable direct-dependency list. The
  security-sensitive direct deps are pinned with `==` to the tested versions
  (`fastapi`, `uvicorn`, `starlette`, `requests`, `httpx`, `schwab-py`, `anthropic`,
  `redis`); everything else stays range-constrained.
- **`requirements.lock`** — the fully-pinned transitive set (every installed package),
  used by CI for byte-reproducible installs. Regenerate after **any** dependency change:

  ```powershell
  .venv\Scripts\python -m pip freeze > requirements.lock
  ```

  (A `freeze` captures the whole venv, so the lock also contains the dev tooling —
  `ruff`, `pip-audit` — which is harmless for CI installs.)

  ⚠ Use **`pip freeze --all`**. Plain `pip freeze` omits `setuptools`, which the lock
  pins deliberately (`pip-audit` audits the environment, not just the imports), so a
  plain regeneration silently drops that pin.
- **`requirements-dev.txt`** — CI/local tooling only (`ruff`, `pre-commit`, `pip-audit`,
  `pytest-cov`).

## Dependency CVE audit

The `audit` job runs `pip-audit` against `requirements.lock`. It is
**`continue-on-error: true`** initially — a freshly-disclosed transitive CVE should
surface for review, not halt all development. Flip it to blocking once the baseline is
clean.

**That condition is now met (2026-08-19):** the baseline went from 31 advisories to
zero, so the flip is a one-line change (drop `continue-on-error` from the `audit` job)
whenever you want the gate. It is deliberately **left non-blocking** — flipping it means
a CVE disclosed against a transitive dependency halts merges until someone bumps it, and
that trade-off is an operator decision, not a consequence of clearing the baseline.

Run it locally with:

```powershell
.venv\Scripts\python -m pip_audit
```
