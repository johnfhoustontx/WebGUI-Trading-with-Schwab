# Tests and test infrastructure

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

## Test infrastructure (2026-08-21)

**The fake bus now matches prod's ONE-Redis semantics.** Every `Bus(fake=True)`
used to build its own `FakeStrictRedis`, so two Bus objects in the same test
shared nothing — while in production every Bus talks to the same Redis. That is
not harmless: **four production modules construct their OWN bus** rather than
receiving one (`options_svc.compute._BRIEFING_BUS`, `trade_svc.compute._BUS`,
`webgui/bus_client._bus`, `_scaffold`'s `the_bus or Bus()`), so a test handing a
handler its own fake bus while the code reached one of those singletons was
reading an **empty cache and passing down the degrade path**.

`shared/bus/client.py` keys **one `fakeredis.FakeServer` per running test**, off
`PYTEST_CURRENT_TEST` with the phase suffix stripped. Shared within a test
(pub/sub and streams cross Bus instances, as in prod), clean between tests — and
**no conftest wiring in any of the ~15 suites** that use it, because pytest
rewrites that variable per test. `reset_fake_bus()` is the explicit hook.

⚠ **If a test needs an EMPTY cache, it must now say so.** Building a second
`Bus(fake=True)` used to give you one for free — a semantic prod never had. Two
tests were relying on exactly that and broke when the fake stopped lying: one
looped over three clock times and only passed because each pass could not see the
cooldown map the previous one wrote. Call `reset_fake_bus()` and rebuild.

**The suite CANNOT open a live SQLite store — enforced at `sqlite3.connect`, not
at the defaults (2026-08-28).** The repo-root **`conftest.py`** carries an autouse
fixture that refuses any connect resolving into a live data directory
(`options-scanner/data`, `options-scanner` itself for `gex_history.db`,
`shared/data`, `webgui/data`, `services/trade_svc/data`, `services/news_svc/data`,
`services/blog_svc/data`).
`tmp_path` and
`:memory:` are unaffected; a test that genuinely must read production shape marks
itself **`@pytest.mark.allow_live_db`**. It is verified to apply to per-app runs
(`cd options-scanner && pytest tests`) since the repo-root `pyproject.toml` is the
configfile, so rootdir — and therefore conftest collection — starts here.

⚠ **That guard covers `sqlite3.connect` and nothing else. The suite CAN reach the
network.** `CLAUDE.md` said until 2026-10-06 that the root conftest also guards
"both HTTP stacks". It does not. A network guard was written on 2026-09-12
(`602cd1c`, "the suite can no longer reach a real HTTP server, and
trade-analyzer was outside every guard", on
`claude/options-strategies-gaps-8636bf`, and its cherry-pick `d4ece94`, on
`claude/app-architecture-audit-2c8f0e`), but both commits sit on side branches
and neither is an ancestor of `main` (checked 2026-10-06). So in every suite but
one, a test that forgets to fake its fetch makes a real request, and nothing
fails.

The one suite with a request guard is the blog service's, in
`services/blog_svc/tests/conftest.py` (it covers `requests`, the only client that
service uses): it stands where `requests` sends, records every call before
refusing it, and fails the test when the test ends. Two details
there are worth copying if the root guard is ever landed. It raises a
`BaseException`, not an `Exception`, because the code under test promises never
to raise and keeps that promise by catching `Exception`: a `RuntimeError` from
the guard was caught, turned into "1 stylesheet could not be fetched", and the
test that forgot its fake passed. And it records the refusal as well as raising
it, because an exception raised in a worker thread or a pool never reaches the
test. The same conftest redirects the blog store's default paths into
`tmp_path`: the store keeps documents and typefaces as plain files, which the
`sqlite3.connect` guard cannot see.

⚠ **The layer is the whole point, and the previous attempt proves it.**
`options-scanner/tests/conftest.py` had carried a fixture written for exactly this
since the first leak, doing `monkeypatch.setattr(signal_db, "DEFAULT_DB_PATH", tmp)`
— and it had **never worked**: those functions are declared
`def f(..., db_path=DEFAULT_DB_PATH)` and **Python binds a default at `def` time**,
so all 13 still resolved to the live path after the patch (measured).
`paper_account_db` was never patched at all. The cost was 24 synthetic signals
(SPY @ 500.00, QQQ @ 430.00, short deltas on an exact 32nd ladder, theta exactly 0)
plus 21 rejected paper orders written into **both** environments on 2026-07-16 and
not found until 2026-08-28, by which point they were feeding backtests. Redirecting
defaults is per-module, per-function, and has to be remembered forever; the connect
guard is one chokepoint every store shares, so a module added tomorrow is covered
without anyone remembering to cover it. **Do not "simplify" it back to patching
paths.** Note `paper_account_db` resolves correctly (`db_path=None` → look up
`DEFAULT_DB_PATH` at CALL time) and `signal_db` does not — prefer the former shape
in new stores.

**`pyrightconfig.json` is a DELIBERATELY NARROW type check** — `shared/bus`,
`shared/contracts`, `shared/config_toml.py`, `webgui/bus_client.py`. That is the
one seam every tier crosses, and where the documented envelope-vs-payload bug
class lives (`cache_get` returns a `CacheEnvelope`, not the payload). Run it with
`.venv/bin/python -m pyright`; it is **clean, and must stay clean**.

⚠ **Do not widen it to the repo.** The services and pages are large, untyped, and
full of deliberately loose payload dicts (contracts model rows as `list[dict]` on
purpose) — switching them on yields thousands of findings nobody will action,
the same failure mode ruff's minimal select list already avoids. Its four
original findings were all **typing-stub artifacts, not bugs**: `redis-py`'s
stubs return `bytes | str` where `decode_responses=True` guarantees `str`. They
are fixed with `cast()` **plus a comment stating the invariant**, never a blanket
ignore.

**The lint gate is enforced at commit (2026-10-03).** `tools/git-hooks/pre-commit`
runs `tools/lint_gate.py --staged` (ruff's `E9,F63,F7,F82` plus, since 2026-10-04,
`F401` unused imports and `F811` redefinitions, over staged Python; those two are
`unfixable` in `pyproject.toml`, so the editor hook reports them and never deletes
an import that was just added or the second of two same-named tests);
`python tools/install_git_hooks.py` points `core.hooksPath` at it, once per
clone, and every worktree shares it. `tools/tests/test_lint_gate.py` runs the
gate over the whole tree, so a suite run catches what a skipped hook let
through. The editor hook (`.claude/hooks/ruff_fix.py`) auto-fixes, then exits 2
with whatever is left, and finds the venv from a worktree. ⚠ The gate was
configured in three places and enforced in none until then: an undefined name
sat on `main` for two weeks. ⚠ The hook script must stay LF (`.gitattributes`).

**Three size ceilings can only be lowered.**
`services/options_svc/tests/test_compute_module_shape.py` holds `compute.py`'s
line count; `webgui/tests/test_render_size.py` holds the lines and
nested-function count of `gamma.render`, `desk.render` and `calculator.render`.
New service code goes in a sibling module — `options_svc/collection_tiers.py` is
the pattern: it imports nothing from `compute`, and `compute` re-exports the
names its own code and its tests use. New page code goes in a module-level
builder that `render` calls and that reads no page state (the Desk's row
builders take their glow class as an argument). ⚠ Tests patch `compute` by
attribute name at roughly 495 sites. A moved function keeps its name on
`compute` for code `compute` itself CALLS; state the moved function READS (a
warned-once set, its logger) must be patched on the new module. ⚠ The Desk's
source-reading tests read `render` plus the builders it calls as one source
(`_painters_source`): a builder moved out of `render` goes on that list, or
those guards stop seeing it.

**Three cross-tier mirrors are now pinned by test, not discipline**
(`shared/tests/test_cross_tier_mirrors.py`, which AST-parses the files and
imports nothing, so it cannot itself trigger the `scoring` collision):
- the five **regime display words**, duplicated in
  `options_svc/market_console.py` and `webgui/pages/regime_mix.py` because those
  tiers cannot import the source. (`sentiment_svc` correctly delegates to
  `market_regime.regime_label` — a test records that it must not grow a fourth
  copy "for symmetry".)
- the **manuals dual registration**. The existing webgui test checked catalog →
  built file; this adds the converse, which was the unguarded half: a manual
  that is BUILT but never listed in `pages/manuals.py` is silently unreachable,
  since that dict is also the serving whitelist.
- the **Strategy Finder's swing defaults** — `options_svc/handlers._SWING_DEFAULTS`
  (what a `swing_scan` command that omits a key runs) against the page's untouched
  scan bar in `pages/options/finder_view.py` (`DEFAULT_DTE`, `RISK_DEFAULT`,
  `DEFAULT_MIN_CREDIT_PCT`), plus the key set `swing.scan_params` sends. They
  drifted for two months (page 0 / All, dict 5–30) under a value test that
  pinned 5. ⚠ The 2026-09-05 attempt moved the floor the OTHER way, because the
  directional family was then built on the nearest expiry and `em_1sd` came from
  the floor; the whole-chain Finder builds every expiry separately and scores each
  against its own expiry's move, so neither reason still holds.

**`scoring/_common.py` now holds `clamp` and `num`.** Measured by AST with
docstrings stripped: **`clamp` had NINE byte-identical private copies and `num`
seven** (six identical, one differently spelled but verified equivalent across 20
inputs before folding it in). That was the "patch one of nine" trap in the very
package where the NaN-guard bug class keeps recurring. ⚠ This is **not** the
thing the NaN section below warns against — that warning is about changing
`clamp`'s NaN *semantics* centrally, and the body here is byte-identical to the
nine it replaced. The NaN policy stays at the call sites, because only the caller
knows whether a missing input means "neutral 50", "floor the magnitude" or
"confidence 0". `_finite` is deliberately **not** consolidated: three functions
share that name and `momentum_regime`'s takes an *iterable*, so hoisting it would
hand someone the wrong one silently. A test records that.

**CI runs every suite, and every suite can fail the build.** `tests/test_ci_covers_every_suite.py` fails when a suite loses its row in `.github/workflows/ci.yml`, a row becomes optional, or a deselect comes back; the four engine folders are linted like the rest (`tests/test_lint_scope.py`), and a `typecheck` job runs pyright on its narrow scope. A new test folder needs a row there.

**`pytest` now defaults to `-rf`** (`[tool.pytest.ini_options]` in
`pyproject.toml`, which every per-app run resolves as its configfile). "Compare
the failing SET, not the count" stops being something you have to remember to ask
for. `-rfs` was considered and rejected: the suites carry a couple of permanent
`importorskip`s, and printing those every run trains people to ignore the summary.

## Tests

Each app's tests run from **inside that app folder** (entrypoints add the repo
root to `sys.path` at runtime):

```bash
(cd schwab-proxy        && ../.venv/bin/python -m pytest tests)
(cd options-scanner     && ../.venv/bin/python -m pytest tests -p no:randomly)
(cd sentiment-dashboard && ../.venv/bin/python -m pytest tests)
(cd trade-analyzer      && ../.venv/bin/python -m pytest .)
(cd portfolio-analyzer  && ../.venv/bin/python -m pytest tests)
(cd webgui              && ../.venv/bin/python -m pytest .)
```

> **The venv lives INSIDE the checkout** (`.venv/bin/python`), so a relative path
> works — until you make a git **worktree**, which has no venv of its own and
> needs the checkout's absolute path — on the VPS
> `/home/administrator/dev/.venv/bin/python`, which IS prod's (see the
> Environments section). The same trap the Windows layout had, in a different
> spelling.
>
> Confining the `cd` to a **subshell** is the tidier habit but is **not**
> load-bearing: the hooks in `.claude/settings.json` resolve their script from
> **`${CLAUDE_PROJECT_DIR:-.}`** (fixed 2026-08-16), so a persistent `cd` out of
> the repo root can no longer wedge the session. Before that fix a single bare
> `cd` made every subsequent tool call fail with a hook error **that could not be
> recovered from** — the hook runs before the command, so the shell could not
> `cd` back. Verified rather than assumed: the hook environment **does** export
> `CLAUDE_PROJECT_DIR`, and hook commands run through a **POSIX shell**, so
> `${VAR:-default}` expands.

The 3-tier services run per folder from the repo root (NOT `pytest services` over
all of them — that puts multiple hyphenated app dirs on `sys.path` at once and
re-triggers the documented `config`/`scoring`/`notifier` module-name collisions).

```bash
# from the repo root, one service at a time. Counts re-measured 2026-08-29
# on LINUX after the migration; compare the failing SET, never the count.
.venv/bin/python -m pytest services/sentiment_svc  # 325 passed / 1 documented-baseline fail
.venv/bin/python -m pytest services/options_svc    # 1216
.venv/bin/python -m pytest services/portfolio_svc  # 32
.venv/bin/python -m pytest services/trade_svc      # 77
.venv/bin/python -m pytest services/market_svc     # 77
.venv/bin/python -m pytest services/news_svc
.venv/bin/python -m pytest services/blog_svc
.venv/bin/python -m pytest shared/bus              # 25
.venv/bin/python -m pytest shared/contracts        # 49 (no app-dir imports — safe together)
.venv/bin/python -m pytest shared/tests            # 89
.venv/bin/python -m pytest tests                   # 69  (env profiles + launcher guards)
.venv/bin/python -m pytest tools/tests             # 816
.venv/bin/python -m pytest deploy/caddy            # 18  (the generated Caddyfile)
```

**Every count above is a 2026-08-20 measurement** -- the accuracy-audit batch (ADX,
put charm, the CT/ET time basis, the VIX blend, the NaN guards) added tests to five
suites. **The five this file previously
flagged as *unverified — measure your own baseline* (portfolio_svc, trade_svc,
the since-removed driver_svc, `shared/bus`, `shared/contracts`) were all measured
that day and had drifted far from their written values (`shared/bus` 15 → 25),
which is exactly what an unverified number does. `market_svc`, `shared/tests`,
`tests` and `tools/tests` were never listed here at all.

**There are no known baseline failures.** As of **2026-08-21** every suite in the
repo runs clean:

| suite | reads |
|---|---|
| options-scanner | **1180 passed / 0 failed / 2 skipped** |
| sentiment-dashboard | **507 passed / 0 failed / 1 skipped** |
| sentiment_svc | **328 passed / 1 xfailed** |
| options_svc | **1218 passed** |
| trade_svc | **79 passed** |

**The long-standing "8-11 permanent failures" were a fiction, and that cost
more than the failures did.** Every one turned out to be a **stale fixture
pinning a constant that had since moved** - a 2-minute collector cadence that
became 1-minute, an 8:30 collection window that became 8:00, a `LOCK_TTL_SEC`
that halved with it, and absolute 2026-05 dates that drifted into the past. They
had been labelled "timing-dependent" and "stale fixtures - do not fix", so nobody
looked. Two consequences worth remembering:

1. **A red baseline hides new failures.** A 9th failure
   (`test_calc_multileg.py::test_per_leg_expiry_back_leg_retains_value_at_front_expiry`,
   whose "far" back-leg expiry of 2026-08-21 simply arrived) appeared the morning
   of the audit and was invisible against an expected count of 8.
2. **Three of the five `TestEarningsAvoidance` tests were PASSING for the wrong
   reason** - once every fixture date is in the past, they all take the same
   early-out. A green test over a stale fixture asserts nothing.

Fixtures are now **derived from the constant or relative to today** wherever the
subject is one (the reasoning `gex_collector.py` already applied to
`LOCK_TTL_SEC`). Nothing was xfail-ed to paper over a failure. The two remaining
**skips** and the one **xfail** are deliberate and named:

- `options-scanner/tests/test_dashboard_*` (2 skips) and
  `sentiment-dashboard/tests/test_apply_sector_perf.py` (1 skip) carry module-level
  `pytest.importorskip`s for the Tk entrypoints **this fork deliberately never
  copied** - they can only pass in the source repo.
- `sentiment_svc/tests/test_compute_regime.py::test_daily_history_wins_over_session_latch`
  is `xfail(strict=True)` over a **real open bug**: the `$VIX1D` session latch
  beats the daily close, so `vix1d_prev` reads 18.0 where the true prior close is
  10.0, inflating `vix1d_spike_pct`. `strict=True` means fixing it FAILS the run
  until the marker is deleted - the xfail is a tracked bug, not a hidden one.

⚠ Two known-flaky behaviours survive: `options_svc`'s
`test_flow_alert_window.py::test_gth_signal_still_fires_at_the_open` was observed
failing once in a full run then passing in isolation and twice more in full runs,
and the date-relative `test_expected_move` cases depend on the run date. Neither
is an expected failure - investigate rather than accept.


**Compare the failing SET, not the count.** A matching total is not evidence of a
clean run: this repo has a documented incident where two real regressions hid
behind two tests flipping to skipped while the total held steady. Run with `-rf`
and diff the node IDs name-by-name. It nearly bit again on 2026-08-09, when
options-scanner's passed/skipped drifted 1351/2 → 1370/3 across a change while the
failure count sat unmoved at 11.

**That drift had TWO independent sources** (the second measured 2026-08-18), and
BOTH are now gone. The first was the `test_gex_collector*` group, recorded here
for a month as "timing-dependent" when in fact it pinned a superseded
`POLL_INTERVAL_MIN` — fixed 2026-08-21 by deriving the expectations from the
constant. The second was a trio of Tk-dependent tests — `test_chart_style_vars.py`,
`test_gex_dex.py:182`, `test_theme.py` — racing on Tk root creation, where
**whichever lost self-skipped, a DIFFERENT one each run**. Two of those three
files were deleted on 2026-08-20 (they tested `build_chart_style_vars` and
`options-scanner/theme.py`, both dead), leaving only `test_gex_dex`'s Tk block —
so the skipped count is now a stable **2**, not a random 2-or-3.

⚠ Because the *identity* of the skipping test varies, **compare the skipped SET
as well as the failed set.** A count-only comparison reads as stable while a
different test silently does not run — which is the same trap as the 2026-08-09
incident, one layer down.

The counts in the block above are indicative, not pinned. **All of them were
re-measured 2026-08-19** on `claude/market-dashboard-updates`, after the dependency
refresh (pillow / setuptools / aiohttp / cryptography) — so they are a post-bump
baseline, which is the useful thing to compare a suspected dependency regression
against. The three large suites, re-measured **2026-08-20** after the dead-code
trim: **webgui 2320 green** (1986 on 08-19; the Bull/Bear map landed in between),
**options_svc 1200 green**, **options-scanner 1172 passed / 8 failed / 2
skipped** — that suite has now FALLEN twice for the same healthy reason: tests
deleted along with their subjects (the legacy CLI + Tk remnants on 2026-08-20
morning, then ~1,850 lines of `gamma_tool` interior + four whole modules that
evening). A dropping count is the one case where the drop is the good news; the
failing SET is what you compare, and it shrank 11 → 8. Also **schwab-proxy 98**
(worth its own line — it is the only suite that exercises the Schwab OAuth stack, so
it is the one that would catch a `cryptography` bump going wrong).

(webgui was 1826 on 2026-08-17 after the four sentiment-screen rebuilds and the
dead-code cleanup — the count FELL from 1912 because ~86 tests were deleted with the
subjects they pinned (the Highcharts scatter/ribbon/RRG builders and the stranded
`pages/sentiment.py` helpers), which is the one situation where a dropping count is
the healthy signal.) **sentiment_svc reads 325 passed / 1 failed** — the documented
`test_daily_history_wins_over_session_latch`.
