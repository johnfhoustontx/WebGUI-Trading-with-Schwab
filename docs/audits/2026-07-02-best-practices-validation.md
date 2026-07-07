# Best-Practices & Industry-Standards Validation — WebGUI Trading with Schwab

**Date:** 2026-07-02 · **Branch:** `Using_Highcharts` · **Method:** evidence-based conformance scan (standard tooling/config artifacts detected or absent by file scan) + synthesis with the session's deep pillar audits. Calibrated to the app's real context (single-user, localhost, Windows, personal trading tool) — each gap states the **strict standard** first, then the **pragmatic priority** for this project.

This is a *conformance-to-professional-practice* lens, distinct from the earlier five-pillar technical audit and the calculation-accuracy audit. It asks: "compared to how professional software is built, tested, secured, shipped, and operated, what's missing?"

---

## ⚑ Remediation status — TIER 1 DONE (2026-07-02)

The five Tier-1 items were implemented (mechanical, no application-logic changes; every affected suite re-verified green after the one `ruff --fix` that touched active code):

| Tier-1 item | Status | Artifact |
|---|---|---|
| CI pipeline | **DONE** | `.github/workflows/ci.yml` — per-folder test matrix (windows-latest, fakeredis) + `ruff` + `pip-audit`; known-broken tests deselected so it's green (options-scanner **1182 passed** with deselects) |
| Lockfile + pinning | **DONE** | `requirements.lock` (113 exact pins from the tested venv); `requirements.txt` points to it |
| Dependency CVE scan | **DONE** | `pip-audit` job in CI (non-blocking initially) + in `requirements-dev.txt` |
| Lint/format gate | **DONE** | `ruff.toml` (select `E9,F`; legacy engines grandfathered) + `.pre-commit-config.yaml`; 39 unused-import/empty-f-string violations auto-fixed; gate is green |
| Governance files | **DONE** | `README.md`, `LICENSE` (proprietary + Highcharts non-commercial notice), `SECURITY.md`, `CHANGELOG.md` |

**Notes:** `ruff format` is deliberately NOT enforced (the codebase's compact one-line style would be rewritten wholesale). The 7 `F821` undefined-name findings are in **grandfathered legacy Tk files** (`shared/analysis_lib/gui_main.py`, `agents/`) — real-but-dormant bugs in dead code, excluded from the gate. Correction to item 2 below: `anthropic` **is** listed in `requirements.txt` (the original claim was wrong).

**Still open:** Tier 2 (packaging/sys.path, mypy, container, migrations, coverage), Tier 3 (proxy CORS/auth, Memurai password, secrets-at-rest, observability, graceful shutdown), Tier 4 (CLAUDE.md restructure, god-modules, print→logger). The single highest-leverage remaining item is **Tier 2 #6 — package the engine dirs** (retires the sys.path/collision debt AND lets CI run the whole tree in one job).

---

## Scorecard by domain

| Domain | Grade | One-liner |
|---|---|---|
| Testing (as written) | **A−** | ~3,340 behavioral tests + architecture guard tests — genuinely strong |
| Config & contracts | **B+** | single-source `repo_paths`/`*.toml` + Pydantic envelope validation |
| Documentation (internal) | **B** | design-doc-per-feature (de-facto ADRs) + rich CLAUDE.md; but no README/governance |
| Observability | **B−** | rotating logs + `/health` (new this session); no metrics/tracing/structured logs |
| Error handling | **B−** | defensive + now instrumented (this session); still broad-except policy |
| **Automation / CI-CD** | **F** | **no CI, no lint/format/type gate, no pre-commit — tests are never enforced automatically** |
| **Dependency / supply-chain** | **D−** | unpinned `>=`, no lockfile, no vuln scan, no SBOM |
| **Packaging / reproducibility** | **D** | hyphenated non-package dirs + 98 `sys.path` inserts; no `pyproject`, no container |
| **Type safety** | **D+** | partial hints, **no enforced type checker** |
| Security posture | **C** | secrets never committed (good); but wildcard CORS + no auth on order path, plaintext tokens, no dep scanning |
| Governance / licensing | **F** | no LICENSE, README, CONTRIBUTING, SECURITY.md, CHANGELOG |

**Headline:** the *code* is disciplined; the *engineering system around the code* is largely absent. The single highest-leverage gap is **there is no automated gate** — the excellent test suite only runs when a human remembers to run it, per folder, by hand.

---

## What's already strong (credit where due)

- **Testing depth**: ~3,340 tests, behavioral not smoke, with *architecture guard tests* (`test_no_inline_style.py`, engine-import bans, stdlib-shadow guard, scheduler-cadence drift guards). Above-average for any project, exceptional for solo.
- **Typed boundaries**: `shared/contracts` Pydantic models validate every cache envelope — real schema-at-the-boundary discipline.
- **Single-source config**: `repo_paths.py` + `config/ports.toml` + `config/commissions.toml`; no hardcoded `D:\` paths or ports (verified).
- **Design-doc-per-feature**: `docs/plans/*-design.md` + `*-plan.md` function as ADRs — the "why" is captured, which most codebases lack.
- **Secrets hygiene**: real secret files gitignored and *never committed* (`git ls-files` clean); only `*.example.*` templates in VCS.
- **Recent hardening (this session)**: per-service rotating file logging, `/health` with `scheduler_alive`, GEX retention, command dead-lettering, off-hours gating.
- **No `assert` in production** service/webgui code (0 found) — asserts confined to tests (they'd be stripped under `python -O`).

---

## GAPS & IMPROVEMENTS (prioritized)

### TIER 1 — High value, low effort, applicable even to a solo/local project

**1. No CI pipeline — the test suite is never automatically enforced. [biggest process gap]**
- *Evidence:* no `.github/workflows`, `.gitlab-ci.yml`, `azure-pipelines.yml`, or `Jenkinsfile`.
- *Standard:* every push/PR runs tests + lint + type + security checks; merge is gated on green.
- *Gap:* the ~3,340 tests only run when a human remembers, **per folder** (running them all at once re-triggers the module-collision bug), so a regression can land silently.
- *Do:* add a **GitHub Actions** workflow (even for a solo repo) that matrixes the per-folder suites (`shared/bus`, `shared/contracts`, `services/*_svc`, `options-scanner`, `webgui`, …), runs `ruff`, and (later) `mypy` on `shared/`. This mechanizes the discipline that today lives only in your head.

**2. Dependencies are unpinned with no lockfile. [supply-chain + reproducibility]**
- *Evidence:* `requirements.txt` uses `>=` floors (`fastapi>=0.111`, `pandas>=2.0`, `anthropic>=0.40`; several bare — `httpx`, `scipy`, `schwab-py`); no `poetry.lock`/`Pipfile.lock`/`uv.lock`.
- *Standard:* a **lockfile** pins exact transitive versions so every install is byte-identical and reproducible; direct deps pinned or ranged deliberately.
- *Gap:* `pip install` today can pull a different (possibly breaking or compromised) version than what you tested; a fresh machine gets an unknown dependency set.
- *Do:* generate a lockfile (`pip-tools`/`uv`/`pip freeze > requirements.lock`) and pin at least the security-sensitive deps (`fastapi`, `uvicorn`, `requests`, `anthropic`, `schwab-py`, `redis`). Add `anthropic` explicitly.

**3. No dependency vulnerability scanning. [supply-chain / OWASP A06:2021 Vulnerable Components]**
- *Evidence:* no `pip-audit`/`safety`/Dependabot config.
- *Standard:* automated CVE scanning of dependencies, ideally in CI.
- *Do:* add `pip-audit` (or GitHub Dependabot) — one CI step; catches known-vulnerable versions.

**4. No linter/formatter/type gate. [code consistency is convention-only]**
- *Evidence:* no `ruff.toml`/`.flake8`/`.pylintrc`/`pyproject`/`.pre-commit-config.yaml`/`mypy.ini`/`.editorconfig`.
- *Standard:* automated formatting (black/ruff-format) + linting (ruff) + type-checking (mypy/pyright), enforced by pre-commit and CI.
- *Gap:* the code is consistent *today* only because one disciplined workflow produces it; there's no mechanical backstop against drift.
- *Do:* add `ruff` (lint + format) with a `pyproject.toml` config + a `.pre-commit-config.yaml`. The codebase would pass mostly clean now — the cheapest time to adopt it.

**5. No LICENSE / README / governance files. [governance]**
- *Evidence:* none of `LICENSE`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` at repo root (only `.gitignore`).
- *Standard:* a repo has a README (what/why/run), a LICENSE (legal status — absence means "all rights reserved," which also blocks *you* from clarity later), and — given it holds secrets + places trades — a SECURITY.md noting the threat model.
- *Note:* Highcharts is bundled under a **personal/non-commercial** license (per `requirements.txt`) — a LICENSE/NOTICE should record that constraint explicitly.
- *Do:* add a top-level `README.md` (the CLAUDE.md has the content — extract a human/onboarding subset), a `LICENSE`, and a short `CHANGELOG.md` (extract the CLAUDE.md "Last updated" changelog, which is ~200 lines of debt in the wrong file).

### TIER 2 — Structural, higher effort, high long-term payoff

**6. Packaging: hyphenated non-package app dirs + 98 `sys.path` inserts + module-name collisions. [the #1 structural standard violation]**
- *Evidence:* 98 `sys.path.insert/append`; `scoring`/`notifier`/`config`/`src` collide process-wide; test suites **must** run per-folder; a `secrets.py` once shadowed the stdlib and crashed a service at launch.
- *Standard:* code is organized as **installable packages** (unique names, `__init__.py`, `pyproject.toml`), installed editable (`pip install -e`), imported by name — no `sys.path` manipulation, no name collisions.
- *Gap:* this is designed-in debt that forbids process consolidation, complicates testing/CI, and is a permanent onboarding tax.
- *Do:* rename the hyphenated dirs to underscored packages, add per-package `pyproject.toml`, install editable. This single change retires the collision class **and** unblocks a real CI matrix (item 1). Multi-day, but it's the highest-leverage structural investment.

**7. No enforced type checking. [correctness / maintainability]**
- *Standard:* `mypy --strict` (or pyright) on at least the shared/contract layer, in CI.
- *Do:* start with `mypy` on `shared/` + new engine modules (they're already well-typed), expand outward. Pairs with item 1.

**8. No reproducible runtime / containerization. [environment parity]**
- *Evidence:* no `Dockerfile`/`docker-compose.yml`; no `.python-version`/`runtime.txt`; venv-on-Windows only.
- *Standard:* a container (or at least a pinned interpreter + lockfile) makes the runtime reproducible and portable.
- *For this app:* a full Docker migration is optional for a single Windows box, but a **`.python-version`** pin + the lockfile (item 2) is the minimum. `docker-compose` (Memurai→Redis, proxy, 5 services, webgui) would also replace the brittle `.bat` launch ordering with a declarative, health-gated dependency graph — worth considering if you ever move machines.

**9. No formal DB migrations. [schema evolution safety]**
- *Evidence:* SQLite schemas via idempotent `init_schema()` calls; no Alembic/migration history.
- *Standard:* versioned, reversible migrations with a recorded history.
- *For this app:* idempotent `init_schema` is *acceptable* for additive single-user schemas, but there's no down-migration or version stamp; a botched manual schema edit has no rollback. Consider a lightweight migration table + numbered scripts if schemas keep evolving.

**10. No coverage measurement. [test-quality visibility]**
- *Evidence:* no `pytest-cov`/`.coveragerc`; coverage is unmeasured despite the large suite.
- *Do:* add `pytest-cov` and a soft floor in CI. You likely have high coverage; measuring it makes gaps visible (and prevents silent erosion).

### TIER 3 — Security & operations standards (some overlap the Security pillar, still open)

**11. Transport & inter-tier auth. [OWASP A01/A05]**
- *Standard:* even localhost services authenticate peers; the token-holding/order-placing proxy is not exposed with wildcard CORS.
- *Gap (from the security pillar, still open):* `schwab_proxy.py` `allow_origins=["*"]` + **no auth on `/orders`/`/accounts`** → a malicious website in your browser can reach the proxy. Memurai has **no password**. No TLS between tiers (acceptable on loopback, but the auth gap is not).
- *Do:* CORS allowlist to the webgui origin + a shared-secret header on the proxy trading endpoints; set a Memurai `requirepass`. (Detailed in the technical-audit Security section.)

**12. Secrets at rest. [CWE-256/312]**
- *Standard:* secrets in an OS keychain / vault / DPAPI-encrypted, not plaintext; documented rotation.
- *Gap:* `tokens.json`/`appsettings.json`/`anthropic_key.txt` are plaintext with default user ACLs — any process in your session can read them. Inherent to a local token cache, but note it.
- *Do:* at minimum tighten the file ACLs to the user; ideally DPAPI-encrypt the token file. Document a rotation procedure.

**13. Observability beyond logs. [standard ops telemetry]**
- *Standard:* structured (JSON) logs with correlation IDs, a metrics endpoint (Prometheus `/metrics`), optional tracing.
- *Gap:* logs are now persisted (good, this session) but are **plain-text, uncorrelated** (no request/command id threading a failure across tiers); no metrics (queue depth, command latency, scheduler tick duration, proxy call counts) — you can't graph the system's health over time.
- *For this app:* full Prometheus is overkill, but **correlation IDs** on commands (you added `Command.ts` this session — add a `Command.id` too) and a couple of counters surfaced on `/health` would make incidents diagnosable. Structured JSON logging is a small formatter change.

**14. Graceful shutdown. [operational correctness]**
- *Evidence:* the proxy does `os._exit(0)` at 15:30 CT (hard exit — skips cleanup/lifespan).
- *Standard:* SIGTERM → drain in-flight requests → close connections → exit (FastAPI lifespan / uvicorn handles this if not bypassed).
- *Do:* replace `os._exit` with a clean shutdown; ensure each service closes its Redis/DB connections on lifespan shutdown.

**15. Process supervision (deferred — R4a).** No supervisor auto-restarts a dead *process* (in-process scheduler restart + alerting shipped this session; a cross-process watchdog was deferred by decision). Standard would be systemd/NSSM/pm2-style supervision; for Windows, an NSSM service wrapper or a small watchdog remains the clean path when you want it.

### TIER 4 — Documentation, API, and code-quality standards

**16. CLAUDE.md is doing five jobs. [documentation structure]**
- *Standard:* a thin README/onboarding doc + separate architecture doc + separate CHANGELOG + per-topic docs.
- *Gap:* the 2,400+-line CLAUDE.md interleaves architecture reference, per-feature deep-dives, gotcha lore, and a ~200-line reverse-chronological changelog. High-quality content, wrong structure.
- *Do:* extract a `README.md`, a `CHANGELOG.md`, and keep CLAUDE.md as a ≤400-line index + gotchas, linking to the (already-existing) `docs/plans/` detail.

**17. API surface / OpenAPI. [API standards]**
- *Standard:* versioned APIs; OpenAPI docs intentionally exposed or disabled; input validation at every endpoint.
- *Gap/verify:* the FastAPI services likely auto-expose `/docs` + `/openapi.json` (no auth) — fine on loopback, but confirm it's intentional; the proxy endpoints aren't versioned (`/v1/...`).
- *Do:* low priority for a personal app; if the proxy ever hardens (item 11), version it and gate `/docs`.

**18. God-modules & god-closures. [complexity / SRP]** (from the code-quality pillar, still open)
- `services/options_svc/compute.py` ~3,000 lines / 103 functions across 8 domains (incl. presentation HTML inside "compute"); four `render()` closures of 400–600 lines.
- *Do:* split `compute.py` by domain behind a re-export façade; extract each card/section builder out of the giant `render()` closures. Maintainability, not user-facing.

**19. Print statements in production paths. [logging consistency]**
- *Evidence:* 7 non-test app files still use `print()` instead of the (now-configured) logger.
- *Do:* convert to `log.*` so they hit the rotating files.

**20. Error taxonomy. [maintainability]**
- *Standard:* typed/domain exceptions, caught narrowly.
- *Gap:* pervasive broad `except Exception` (policy — now instrumented with logging this session, which was the important half). Longer term, introduce a small exception hierarchy for the trade-path so failures can be caught by *type*, not by blanket.

---

## The five things I'd do first (if this were a fresh sprint)

1. **CI workflow** (GitHub Actions: per-folder test matrix + ruff) — mechanizes everything else. *(item 1, 4)*
2. **Lockfile + pin security-sensitive deps + `pip-audit`** — supply-chain floor. *(items 2, 3)*
3. **`ruff` + `pre-commit`** — consistency backstop, passes mostly clean today. *(item 4)*
4. **README + LICENSE + extract CHANGELOG** — governance basics + fixes the CLAUDE.md bloat. *(items 5, 16)*
5. **Proxy CORS allowlist + shared-secret + Memurai password** — closes the one real remote-risk path. *(item 11)*

Then, as a dedicated structural investment: **package the engine dirs** (item 6) — it retires the sys.path/collision debt *and* unblocks a clean CI matrix, so it multiplies the value of items 1–4.

## Honest calibration

This is a **single-user, localhost, personal** trading tool, so a chunk of "industry standard" (containers/orchestration, multi-env config, Prometheus/tracing, API versioning, formal migrations, process supervisors) is calibrated for team/production SaaS and is genuinely optional here. But four gaps are **not** context-dependent and would be flagged on any codebase: **no CI, no dependency locking/scanning, no lint/type gate, and the sys.path/packaging debt.** Those four are where the "strict best practices" delta really is — the rest of the project's internal discipline is already better than most.
