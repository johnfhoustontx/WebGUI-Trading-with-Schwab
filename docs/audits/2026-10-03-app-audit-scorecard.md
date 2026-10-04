# Application Audit — Scorecard and Open-Items Checklist

| | |
|---|---|
| **Audit date** | 2026-10-03 |
| **Commit audited** | `c89b1d5` (tip of `main`) |
| **Re-audit** | 2026-10-03, of the 54 commits `c89b1d5..ad873ec` (the proxy's local market-data store, collector tiers, chain carry, scanner wide fetch). See section 1.5 and checklist 4.8 |
| **Scope** | Whole repository: ~170,000 non-test Python lines, 441 source files, 629 test files |
| **Categories** | Code Quality · Performance · Architecture · Security · Accuracy |
| **Previous audits** | [2026-07-01 technical](2026-07-01-technical-audit.md) · [2026-07-01 calculation accuracy](2026-07-01-calculation-accuracy-audit.md) · [2026-07-02 best practices](2026-07-02-best-practices-validation.md) |

This document is the tracker. Section 1 is the scorecard, section 2 is how the five
categories depend on each other, section 3 is the order to work in, and section 4 is
the checklist — one row per finding, with a `Status` column to update as items close.

## How this audit was done, and how far to trust it

Six read-only auditors ran in parallel (accuracy was split in two: options math and the
paper money path; indicators, scoring and the factor model). Nothing in the repository
was changed, no service was started, and nothing touched the production server, Schwab
or Claude. That last point sets the limit of the evidence:

- **Code facts are verified.** Every finding cites a file and line the auditor read.
- **Numerical findings were reproduced** with scripts against the real functions, on
  synthetic chains and seeded random-walk price series. No market data was available.
- **Anything about the running server is inferred** — core count, Redis configuration,
  file modes, whether the proxy secret is set, how often Schwab sends a bad value.
  Section 5 lists the exact commands that settle each one.
- **Performance timings are synthetic** micro-benchmarks on a Windows workstation.

The `Conf` column in the checklist records this per finding:

| Code | Meaning |
|---|---|
| `R` | Reproduced numerically or by running the code |
| `C` | Confirmed by reading the code path |
| `+M` | Additionally re-read and confirmed in the code by the coordinating session |
| `I` | Inferred. Measure before building the fix |

Earlier audits of this application had several rationales fail when measured against
production data. Treat every `I` row as a claim to check, not a task to start.

---

## 1. Scorecard

### 1.1 Category scores

Scores are out of 10. "July" is the 2026-07 audit's score for the nearest equivalent
pillar. The two audits used different auditors and a wider scope this time, so read
the trend as indicative only — see the notes under the table.

Counts are **open** rows after the re-audit. "At audit" is the burden at `c89b1d5`.

| Category | Score | July | Critical | High | Medium | Low | Open burden | At audit |
|---|---|---|---|---|---|---|---|---|
| Code Quality | **5.5** | 7.0 | 0 | 2 | 7 | 8 | 30 | 34 |
| Performance | **6.8** | 6.5 | 0 | 0 | 7 | 12 | 26 | 27 |
| Architecture | **5.5** | 7.0 / 5.0 | 0 | 0 | 8 | 9 | 25 | 36 |
| Security | **7.5** | 7.0 | 0 | 0 | 6 | 12 | 24 | 29 |
| Accuracy | **6.4** | 7.7 | 0 | 0 | 26 | 20 | 72 | 102 |
| **Overall** | **6.3** | 6.5 | **0** | **2** | **54** | **61** | **177** | **228** |

- **Open burden** is the trackable number: Critical 8, High 4, Medium 2, Low 1, summed
  over open rows. Subtract a row's weight when its status becomes Done, Accepted or
  Refuted. Category scores are re-judged only at the next audit.
- **The rise from 228 to 270 is not a regression.** The re-audit added 34 rows and
  closed or reduced two. Of the 49 points added, **36 are gated behind a switch that
  ships off** (they bite only once the store, the collector tail or the wide fetch is
  turned on) and **13 are live now**. Burden that is live today: 228 − 7 + 13 = **234**.
- **2026-10-03, later the same day: the three Critical and ten High Accuracy rows
  were fixed** (64 points; see section 8). Two Medium rows were opened while fixing
  them (AC-19 and AC-60, 4 points), so the open burden is 270 − 64 + 4 = **210**.
  Twelve of the thirteen were in the original audit, so the live-today burden
  falls by 60 and rises by the 4 new points: 234 − 60 + 4 = **178**.
- **2026-10-03 to 10-04: eight of the ten remaining High rows were fixed** (SE-01,
  SE-02, AR-01, AR-02, AR-03, AR-04, PF-02, CQ-02; 32 points) and one Low with them
  (SE-08; 1 point), so the open burden is 210 − 33 = **177**. The two High rows
  still counted are CQ-03 and CQ-04, both `In progress`: each has a first slice
  committed and a ceiling test that stops the code regrowing, and each is several
  days of work to finish. No row was opened. All nine closed rows were in the
  original audit, so the live-today burden is 178 − 33 = **145**.
- **Scores are unchanged by the re-audit** except one sub-dimension (static analysis,
  4.0 → 4.5). As shipped the change is dormant or a small improvement; its risk is in
  switching it on, which section 1.5 covers.
- **Architecture** combines July's "Scalability and Architecture" (7.0) and
  "Reliability" (5.0) pillars.
- **Accuracy** is the mean of the two halves (options and paper 6.0; indicators and
  scoring 6.8). The fall from 7.7 is mostly scope: July checked formulae, and the
  formulae still check out. This audit also followed the numbers through settlement,
  exits and absence handling, which is where the defects are.
- **Code Quality** fell because the structural debt July flagged grew 2 to 3.5 times
  while the lint gate that was added is not being enforced.
- Four more accuracy items are carried forward from earlier audits (section 4.7).
  They are not in the counts above.

### 1.2 Sub-dimension scores

| Category | Sub-dimension | Score | Basis |
|---|---|---|---|
| Code Quality | Modularity, size, complexity | 3.5 | `options_svc/compute.py` 3,039 → 10,548 lines since July; 61 functions over 150 lines; `gamma.render` 1,484 lines |
| | Error-handling discipline | 6.0 | 1,007 broad `except Exception` (414 silent), but only 2 large silent guards left in `services/` |
| | Duplication, single-sourcing | 5.5 | 31 copies of `_num` with 16 different behaviours; byte-identical duplication is small |
| | Static analysis and enforcement | 4.5 | Was 4.0: the lint gate was red at audit and is green after `ade9c87`. Still no git hook installed; type check covers about 1% of the code |
| | Test quality and coverage | 8.5 | About 14,200 test functions; 20 of 417 modules never named in a test |
| | Docs, config, dependency hygiene | 6.0 | `CLAUDE.md` about 300 KB against its own 100 KB target; lock file complete in both directions |
| Performance | Interface responsiveness | 8.0 | Every large read is off the event loop and every poll is version-gated |
| | Service throughput and scheduling | 6.0 | Six blocking pollers share one thread pool with all scheduler work; one serial queue for about 35 command types |
| | Data-store efficiency | 7.0 | Good retention and compression; history keys rewritten whole each minute; one unbounded table |
| | External-API budget | 6.0 | About 75–79 thousand Schwab calls per weekday; no headroom on the shared limit at quarter-hours |
| | Memory | 7.5 | Service caches bounded; each browser tab holds its own parsed copy of large payloads |
| | Scalability headroom | 5.0 | Twice the watchlist breaks the 1-minute collection cadence |
| Architecture | Tier and process boundaries | 8.0 | 914 web-app imports checked; one off the allow-list; no service imports another |
| | Contract and data-shape integrity | 4.0 | About 16 of 105 cache writes validated; no read-side validation |
| | Fault tolerance and command semantics | 5.5 | Good per-command isolation; destructive commands not replay-guarded; dead-letter list unread |
| | Data durability and recoverability | 5.0 | Sound backup mechanics; no restore procedure; login store not backed up |
| | Deploy and change safety | 4.0 | Promote stops production before the steps that can fail; no rollback; no staging |
| | Observability | 5.5 | Failure detection exists only in an open browser tab |
| | Concurrency correctness | 6.0 | Clean lock ordering; the paper books have no mutual exclusion |
| Security | Authentication and session | 8.5 | Argon2id, replay-protected second factor, correct cookie flags |
| | Public-origin isolation | 8.0 | Default-deny middleware; allow-list builders held under adversarial input |
| | Input handling and injection | 8.5 | No shell execution, no SQL from external input; one proxy path traversal |
| | Secrets management | 6.5 | History clean; tokens leak to logs on connection errors; secret check fails open |
| | Network exposure and edge | 7.0 | Everything binds loopback; proxy published to the tailnet mostly unauthenticated |
| | Dependencies and supply chain | 6.5 | Six open advisories; audit job non-blocking |
| Accuracy | Pricing core | 8.5 | Put-call parity to 3.6e-15; every Greek matches finite differences |
| | Time basis | 6.5 | Helpers agree on expiry day; differ by one hour across a daylight-saving change |
| | Dealer-positioning math | 7.5 | Formula exact to the cent; one bad Schwab value produces a 6,300× cell |
| | Trade economics and selection | 7.0 | Payoff exact for 11 structures; long-dated calendars off by up to 47% |
| | Paper profit-and-loss and risk accounting | 4.0 | Cash identity holds; all three books settle expiry at the wrong time or price |
| | Indicator math | 8.5 | EMA, RSI, ADX, MACD, VWAP all match independent implementations |
| | Sentiment and regime scoring | 6.0 | Regime simplex sound; three call-site errors on the Day gauge |
| | Robustness to missing or bad data | 6.0 | Hardened where earlier incidents happened; open elsewhere |
| | Factor-model validity | 7.0 | Live scoring matches the fit; walk-forward has no purge |
| | Portfolio math | 5.5 | Labels shifted one horizon; horizons mixed past 12 months |

### 1.3 Ten findings that matter most

| # | ID | Finding | Why it ranks here |
|---|---|---|---|
| 1 | SE-01 | The proxy's `/passthrough` route reaches the real brokerage account API without the shared secret. **Fixed `88b6ebc`** | Highest-value asset; one-hour fix |
| 2 | SE-02 | The proxy secret check fails open, and an unused endpoint can place real orders. **Fixed `88b6ebc`**; production has no secret set, so its account routes answer 503 until one is (SV-01) | The application is paper-only; nothing needs this endpoint |
| 3 | AC-01 | Captured signals are closed as "expired" on expiry morning. **Fixed `f0ed8fd`**; 307 of 930 stored outcomes carry the old behaviour (SV-12) | Corrupts the outcome data every calibration and measured study rests on |
| 4 | AC-02 | The paper Account and Ledger settle expiries the next trading day at that morning's price. **Fixed `f0ed8fd`** | Booked profit and loss is wrong for every trade held to expiry |
| 5 | AC-03 | Income positions can never be closed by a rule. **Fixed `e0db4d6`** | The 50% target and 21-day exit described as live never execute |
| 6 | AR-01 | Promote takes production down before any step that can fail, with no rollback. **Fixed `1cd9a2d`** | Every other fix in this document ships through it, with no staging |
| 7 | CQ-01 / CQ-02 | A live undefined-name bug has sat on `main` for two weeks because the lint gate is not enforced. **Fixed `ade9c87`, `9e3b700`** | The mechanical backstop is off |
| 8 | AC-40 | A data outage publishes "Strong Bear", pushes it to the phone and blocks put credit spreads. **Fixed `7cbdecb`** | A failure that looks like a confident reading |
| 9 | AR-02 | Paper-book changes are not serialized between the scheduler and the command consumer. **Fixed `95319de`** | A double close overwrites realized profit and loss |
| 10 | AR-03 / AR-04 | Failures are only visible in an open browser tab; restore is undocumented and the login store is not backed up. **Fixed `007c63b`, `dbfc3e5`** | Continuity |

### 1.4 Closed as fixed, found not fixed

This application has a history of audit items being closed while the defect remained.
Four more instances surfaced:

| Item | What the record says | What the code does |
|---|---|---|
| CQ-01 | Docstring: `evaluate_regime` is "eagerly imported at module top" | The import was removed on 2026-09-19; the name is undefined and the error is swallowed. **Fixed in `ade9c87`, confirmed at re-audit** |
| CQ-02 | `pyproject.toml`: the rule set "passes CLEAN on the current tree" | `ruff check .` fails. **Fixed in `ade9c87`** (the tree) **and `9e3b700`** (a commit hook and a test now hold it there) |
| AC-03 | Income positions were given marks and exit rules on 2026-09-11 | The repricer was repaired; the broker that fills the close was not. **Fixed in `e0db4d6`**, with two tests that drive the real broker |
| AC-40 | The absent-composite fix publishes no band when there is no reading | The main producer returns `"0.00"`, which is treated as a real reading. **Fixed in `7cbdecb`**, with tests driven from a client that fails every call |

### 1.5 Re-audit: the proxy's market-data store (commits `c89b1d5..ad873ec`)

**What changed.** The proxy now keeps what it fetches in memory (chains, quotes, daily
bars) and a gateway decides per request between a stored answer and a call to Schwab.
On top of it: a two-tier collector that can re-price ("carry") a stored chain for
watchlist-only symbols between real fetches, and a scanner option to fetch one wide
chain per symbol. About 3,200 lines of source and 9,700 of tests. **Everything ships
dormant**: the store in `shadow` (every request still goes to Schwab; the proxy only
counts what it would have reused), the collector tail at 1, the wide fetch off. The
CHANGELOG records it as promoted in that state.

**How it was re-audited.** Four read-only auditors on the diff. The store and the
carry were driven with a fake Schwab, a controlled clock and synthetic chains. Nothing
was checked against real Schwab responses, so "a cut equals a narrow fetch" and "the
daily bar is final ten minutes after the close" remain assumptions (CQ-105).

**As shipped: sound.** The default paths are unchanged (the collector and scanner make
the same requests as before). The handlers return byte-identical bodies and the same
status codes. Nothing stale is served because Schwab failed. No account or order data
reaches the store. Proxy processor time per chain fell (synthetic: 257 ms to 129 ms in
shadow on an index-sized chain). The Rescue regime bug is fixed with tests that call
the real function. 11 of the 34 new findings are live today; none is above Medium.

**The risk is in switching it on.** Readiness per switch:

| Switch | Verdict | Do first |
|---|---|---|
| Store on — quotes | Ready with one condition | Clamp the config age limits (AC-104) |
| Store on — chains | Not until four conditions are met | Make shadow's chain verdict compare more than the contract set, and log what differed (AC-103). Have the collector send its age limit in shadow so its repeats stop counting as savings (AC-102). Give the money-path callers an age limit: Rescue's stale-price guard and paper fills (AC-140, AC-141). Decide what a narrow request gets when the widened fetch fails (AR-100) |
| Store on — daily bars | **Not yet**, although the rollout lists bars first | Shadow cannot see the only bar that goes stale (AC-100). A pre-close bar is served as the day's bar until ten minutes after the close, with no caller override (AC-101) |
| Collector tail above 1 | Ready with conditions; use 3 or 5 only | Run `tools/measure_chain_carry.py` on an expiration Friday through 14:30–15:00 CT. Close or accept the cap bias (AC-120). Mark carried rows in storage (AC-121). Reject intervals 2 and 4 (AC-125) |
| Scanner wide fetch | Ready with conditions | Read shadow's chain subset counts first (after AC-103). Watch the first session for repeated fallbacks (AC-124) |

**Earlier findings the change touched.**

| ID | Status after the change |
|---|---|
| CQ-01 | **Done** (`ade9c87`): the name is bound, the degrade is counted, five tests call the real function |
| CQ-02 | Partly: `ruff check .` passes again. Enforcement is unchanged — no hook installed |
| PF-01 | Partly: the four market-data routes return bytes, so the framework's encoder pass is gone. Lowered from High to Low; `/instruments`, `/passthrough` and the account routes still return dictionaries |
| PF-02, PF-10 | Unchanged as shipped. The mechanism that addresses them exists and is switched off |
| CQ-03, CQ-09 | Slightly worse: `compute.py` 10,548 → 10,668 lines, `schwab_proxy.py` 1,819 → 2,018, `CLAUDE.md` 302 → 311 KB |
| CQ-05, CQ-07, CQ-08 | Unchanged, with more surface: the new collector and scanner tests run in the non-blocking lane; five more private number helpers (all strict); the new modules are outside type-check scope |
| SE-01, SE-02, SE-07, SE-12 | Untouched by the diff |
| AC-09, AC-15, AC-52 | Unchanged. With the tail on, a bad Greek on a tail symbol persists for the whole carry interval |

---

## 2. How the categories depend on each other

### 2.1 Category-on-category effects

Read across: a change made for the **row** category has this effect on the **column**
category. Counts are from the per-finding cross-impact assessments in section 4.

| Change made for ↓ / effect on → | Code Quality | Performance | Architecture | Security | Accuracy |
|---|---|---|---|---|---|
| **Code Quality** (16 fixes) | — | Neutral in all 16 | Helps in 3. Consolidating numeric helpers needs a new shared module on the web tier's import allow-list | Helps in 6. **Risks in 1**: splitting page `render()` functions can break guards that are proven by reading the source | Helps in 11. **Risks in 3**: splitting `compute.py`, consolidating `_num`, and moving constants to config all change behaviour silently if done mechanically |
| **Performance** (15 fixes) | Helps in 3. **Risks in 4**: lane splitting, server-side paging, a second storage shape, shared cached objects | — | Helps in 1. **Risks in 3**: priority lanes and command lanes add contracts; lane splitting removes the single-consumer assumption the paper book relies on | Helps in 3: bounds what the public origin can exhaust | Helps in 4 (fewer lost collection minutes). **Risks in 4**: any caching or thinning trades freshness for speed |
| **Architecture** (17 fixes) | Helps in 6 | Helps in 6. Risks in 2, both slight (validation cost, Redis persistence cost) | — | Helps in 4. **Risks in 2**: backing up more secrets and adding a notifier both widen where credentials live | Helps in 10. None risk it |
| **Security** (18 fixes) | Helps in 9. Risks in 1 (a key allow-list to maintain) | Helps in 2 | Helps in 5. **Risks in 1**: sandboxing the public process can break the single-target promote and restart flow | — | Helps in 2. Risks in 1 (dependency bumps can regress the Schwab client) |
| **Accuracy** (38 fixes) | Helps in about 30. Risks in 1 (dividend yield threaded through many call sites) | Helps in 2. Risks in 1 (longer price history for portfolio comparison) | Helps in 9. Risks in 2 (dividend source; per-product settlement table) | Helps in 2 | — |

What the matrix says:

1. **Accuracy fixes are the safest changes in this document.** Most are small, local,
   and help or leave alone every other category. Their cost is elsewhere: they change
   live numbers (see 2.3).
2. **Code-quality refactors are the changes most likely to damage accuracy.** The
   reasons are specific to this codebase: tests patch `compute` by attribute name at
   495 sites, module constants bind at import, and each of the 31 `_num` copies has
   callers that lean on its particular quirks.
3. **Performance fixes that add concurrency or caching carry the most cross-category
   risk** — to architecture (assumptions about a single consumer), accuracy
   (staleness) and code quality (more moving parts).
4. **Architecture fixes are nearly all upside for the other four.** The two security
   risks are about where secrets live, and both are manageable.
5. **Security fixes are mostly isolated** to the proxy, the login route and the edge.
   The exceptions are sandboxing and dependency bumps, which need the promote path to
   be safe first.

### 2.2 Coupled clusters — fix together, or in a fixed order

Each cluster is a set of findings from different categories with one root. Fixing one
member alone either does not work or moves the problem.

| Cluster | Members | The shared root | Order |
|---|---|---|---|
| **A. Failure looks like normal data** | CQ-01, CQ-06, AR-03, AR-06, AC-40, AC-49, AC-55 | A swallowed error or a missing input returns a plausible value, `/health` reports up regardless, and detection needs an open tab | AC-40 and CQ-01 first (wrong outputs), then AR-06 (make `/health` honest), then AR-03 (tell someone) |
| **B. Settlement and time basis** | AC-01, AC-02, AC-06, AC-15, AC-17, AC-18, AR-19 | Three books and two helpers each decide "when does this expire, and at what price" separately | One shared settlement helper; AC-01 and AC-02 together; AR-19 (half days) extends the same helper |
| **C. Paper-book integrity** | AR-02, AR-05, AC-13, AC-14, AC-16, PF-04 | Book mutations are neither serialized nor guarded on status; safety rests on there being one consumer | AR-02 **before** PF-04. Splitting the command queue first would turn a rare race into a common one |
| **D. Bad values from the data feed** | AC-09, AC-10, AC-52, AC-57, AC-08, CQ-07 | NaN, `-999` and zero-bid reach scorers and exit rules; 31 helper copies disagree about what passes | Guard at the call sites first (small accuracy fixes). Consolidate helpers (CQ-07) only afterwards, one behaviour group at a time |
| **E. The shared 5-requests-per-second budget** | PF-01, PF-02, PF-03, PF-10, SE-11, PF-13 | Collection, scans, the public tools and restarts all draw on one limiter with no priority | PF-01 and PF-03 are free wins. PF-02 (priority lane) before any feature that adds chain fetches. SE-11 bounds the public share |
| **F. Change safety** | AR-01, AR-10, CQ-02, CQ-05, SE-14 | No staging, a promote with no rollback, a lint gate that is off, a continuous-integration run that gates nothing | These are enablers: do them before anything risky (dependency bumps, sandboxing, refactors) |
| **G. The proxy** | SE-01, SE-02, SE-07, SE-12, PF-01, AR-09 | One 1,800-line process holds the brokerage token, exposes generic routes, and re-encodes every payload | One pass over the proxy can close all six |
| **H. Public-origin resource exhaustion** | SE-03, SE-09, SE-10, PF-07, PF-05 | The unauthenticated process shares a user, a Redis read grant and memory behaviour with private state | SE-03 first (login body), then PF-07, then SE-10 and SE-09 |
| **I. The large modules** | CQ-03, CQ-04, CQ-10, AR-08, AR-18 | `compute.py` and the page `render()` functions hold risk controls, presentation and guards in one place | Last. Do after clusters B, C and D so the code being moved is already correct and has independent-reference tests |
| **J. Store freshness** (re-audit) | AC-140, AC-141, AC-143, AC-101, AC-104, AC-106, AR-100, PF-100 | A performance feature (answer from memory) changes what "a fresh fetch" means for every caller, and only the collector states what it needs. The saving and the staleness are the same number | Before the store is switched on: each money-path caller sends its own age limit; then clamp the config; then switch. Every limit a caller tightens gives back some of the saving, so decide caller by caller |
| **K. Shadow validity** (re-audit) | AC-100, AC-102, AC-103, CQ-100, CQ-105 | The decision to switch on rests on shadow's counts, and shadow compares less than it appears to, counts some savings that will not happen, and has no reader in the app | Fix before reading the counts, not after. A captured Schwab payload (CQ-105) is what turns the auditors' assumptions into tests |

### 2.3 Fixes that move live numbers

These accuracy fixes change what the application reports or decides on the day they
land. Each needs a before-and-after check, and several invalidate earlier measurements.

| Fix | What moves | Check after landing |
|---|---|---|
| AC-01, AC-02 | Every outcome and booked result for trades held to expiry | Count how many historical outcomes closed before 15:00 CT on their expiry date. The score calibration, the entry-volatility-rank study and the profit-lock ladder replay were all computed on these outcomes and should be re-run. **Counted (SV-12): 307 of 930.** The fix is forward-only; those rows are unchanged and can be re-settled from daily closes |
| AC-03 | Income positions begin closing at target and at 21 days | Watch the first manage cycle; expect several closes at once |
| AC-04 | Iron-condor quantity and reserved risk fall | Compare the day's condor signals before and after |
| CQ-01 (done) | Narrower than first stated: only the on-demand advisory gains +6 heat. State and candidate ranking do not read the regime, and the at-risk board passes none | Compare one advisory before and after |
| AC-101 | Interacts with AC-02: a settle slot at about 15:05 CT that reads the day's close from `/pricehistory` lands inside the window where a stored pre-close bar is served | Fix AC-101 first, or have the settle call bypass the store |
| AC-41, AC-42, AC-50 | Day, Week and Month gauge readings and the five-state market state | Run old and new side by side for a session before switching |
| AC-47 | Volatility sub-scores; the bridge snapshot test fixture moves | Regenerate the fixture deliberately |
| AC-11 | Probability-of-profit gate on long-dated Finder rows | Re-check the tuned gate thresholds |
| AC-53 | Reported out-of-sample information coefficient falls slightly | Refit; compare against the ship gate |

### 2.4 Change-impact gates

When a change touches the left column, re-verify the right column before it ships.

| If you change | Re-verify in other categories |
|---|---|
| Anything in `services/options_svc/compute.py` | **Accuracy**: run the options service suite and compare the failing set. **Code Quality**: `ruff check .` — a removed import can break a function thousands of lines away (CQ-01) |
| A module-level constant, or move one to config | **Accuracy**: add the monkeypatch-and-reload test; a plain equality test proves nothing |
| A numeric helper (`_num`, `_finite`, `_clamp`) | **Accuracy**: feed NaN, None, 0, `-999`, infinity and a boolean through every caller you touched |
| Command handling, queues or thread pools | **Architecture**: the paper book's read-then-insert is safe only with one consumer (AR-02). **Accuracy**: a command older than 180 seconds is refused as stale |
| Anything that caches or thins data | **Accuracy**: what reads it, and how stale may that reader be? Exit rules and marks must not read a cache |
| A page `render()` function on a published screen | **Security**: the public/private branches and `may_enqueue` guards are proven by tests that read the source |
| The proxy | **Security**: every new route needs the secret dependency. **Performance**: the proxy's CPU time is shared by every service |
| A setting in `config/marketdata.toml` (no restart needed, editable from Settings) | **Accuracy**: which callers send no age limit and so inherit this value (section 1.5 and cluster J). **Architecture**: go through `shadow` first, and read the counts only after cluster K is fixed |
| Any new caller of the proxy | **Accuracy**: state the oldest answer it accepts (`maxAge=0` for fills, guards and settlement). **Performance**: set a caller label, or it is counted as `unknown` |
| `requirements.lock` | **Architecture**: no staging exists, so run the proxy suite and dry-run the install against the production environment first |
| systemd units or the promote script | **Security**: memory caps and sandboxing. **Architecture**: test a failed promote, not only a successful one |
| A cache view's shape | **Architecture**: only about one write in seven is validated; find every reader by hand |
| The backup file list | **Security**: the archive is the largest single collection of secrets; confirm it is still encrypted before upload |

---

## 3. Recommended order of work

Ordered by dependency first, then by risk reduced per hour.

| Wave | Purpose | Items | Why this position |
|---|---|---|---|
| **0** | Make change safe | CQ-01, CQ-02, CQ-05, AR-01, AR-10 | Everything after this ships through a promote with no staging. About a day in total |
| **1** | Close the exposed surface | SE-01, SE-02, SE-08, SE-03, SE-05, SE-07, AR-05 | Each is under an hour and isolated. Run the section 5 server checks alongside |
| **2** | Money-path correctness | AR-02, AC-01, AC-02, AC-03, AC-04 (measure first), AC-08, AC-09, then re-run calibration | The paper record is the product. AR-02 goes first because the settlement fixes add a scheduler slot that writes the book |
| **3** | Wrong numbers on decision screens | AC-40, AC-41, AC-42, AC-43, AC-44, AC-45, AC-05, AC-06, AC-10, AC-52 | All small; several move live readings (section 2.3) |
| **4** | Continuity | AR-03, AR-04, AR-06, AR-09, AR-12, AR-07 | Restore procedure and unattended alerting |
| **5** | Performance | PF-01, PF-03, PF-08, PF-09, PF-11, then PF-02; PF-04 only after AR-02 | The first five are small and low-risk |
| **6** | Remaining accuracy and security | AC-07, AC-11, AC-13, AC-46 to AC-55, SE-04, SE-06, SE-09 to SE-18 | Medium items, including the ones that need a refit or a design decision |
| **7** | Structure | CQ-07, CQ-10, CQ-08, CQ-09, CQ-03, CQ-04, AR-08, AR-14 | Last on purpose: refactor code that is already correct and has independent-reference tests |
| **S** | Before switching the market-data store on (independent of the waves above; do when the switch is wanted) | Shadow validity: AC-100, AC-102, AC-103, CQ-100, CQ-105. Then freshness: AC-104, AC-140, AC-141, AC-101, AR-100. Then per switch: section 1.5 | The store ships dormant, so these block nothing today. They block the switch |

---

## 4. Checklist

### How to use it

- **Status** values: `Open` · `In progress` · `Done` (add date and commit) ·
  `Accepted` (will not fix; add the reason) · `Refuted` (measured and found false).
- When a row leaves `Open`, subtract its weight from the Open burden in section 1.1
  and add a line to section 8.
- **Effort**: S is under an hour, M is about half a day, L is several days.
- **Cross-impact columns** show what fixing the row does to each category:
  `+` helps · `!` risks · `±` both · `·` neutral · `■` the row's own category.
  Columns are Code Quality (CQ), Performance (PF), Architecture (AR), Security (SE),
  Accuracy (AC).

### 4.1 Accuracy — options math and the paper money path

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-01 | Critical | R+M | Captured signals close as EXPIRED on expiry morning. The gate is `dte <= 0` with no 15:00 CT check. They close at the live mark, or at intrinsic against the **entry** price when unmarkable. Two tests pin the behaviour. Feeds `signal_outcomes` and the nightly calibration | `services/options_svc/compute.py:3534-3551`; `tests/test_compute.py:1819,1830` | Gate on `paper_engine.should_settle`; settle at intrinsic against the session close; defer when no close is available; rewrite both tests against an independent expectation | M | + | · | + | · | ■ | **Done** 2026-10-03, `f0ed8fd` |
| AC-02 | Critical | R+M | Paper Account and Ledger settle expiries the next trading day at that morning's live price. The cycle runs 09:00–14:00 CT; settlement needs 15:00 or later. Reproduced: true +$98.70 at Friday's close booked as −$401.30 on Monday **Re-audit:** if the fix reads the close from `/pricehistory` at about 15:05, it must bypass the store or wait for AC-101. | `services/options_svc/scheduler.py:207`; `options-scanner/paper_engine.py:633-649,752-753`; `compute.py:2966` | Add a settle slot at about 15:05 CT; for a past expiry settle against that date's daily close, not a live quote | M | + | · | + | · | ■ | **Done** 2026-10-03, `f0ed8fd` |
| AC-03 | Critical | R+M | Income positions (short put, covered call) can never be rule-closed. `simulate_fill_price` raises for anything but PCS, CCS, IC, so each exit is rejected every hour and a rejected order row is written | `options-scanner/paper_broker.py:49-72`; `paper_engine.py:844-856` | Add a single-leg branch using `fill_model.realistic_single_fill` | S | + | + | · | · | ■ | **Done** 2026-10-03, `e0db4d6` |
| AC-04 | High | C+M; frequency I | Iron condors with unequal wings are sized and reserved off the put width while max loss uses the wider wing. Reproduced for 5-wide and 10-wide: booked $560, true $1,560 | `options-scanner/scanner_engine.py:1401,1424`; `paper_engine.py:265,294` | **Measure first**: count condors in `signals.db` whose wing widths differ. Then emit `width = max(...)` or size from `entry_max_loss` | S | + | · | · | · | ■ | **Done** 2026-10-03, `fe4402e` |
| AC-05 | High | R | Calculator MAX RISK shows a finite figure for unlimited-risk structures (short straddle $4,310). The scan covers only 0.5× to 1.5× spot. Also on the public calculator | `options-scanner/options_calculator.py:720-735`; `webgui/pages/options/calculator.py:726-734` | Use the tail-slope rule `payoff_metrics` already has; scan to zero when net short puts | S | + | · | · | · | ■ | **Done** 2026-10-03, `7c8d563` |
| AC-06 | Medium | R | The two time-to-expiry helpers differ by one hour across a daylight-saving change. Calculator expiry column showed −$287.19 against a true −$300.00 | `compute.py:8173-8183`; `options_calculator.py:74-79,1085` | Subtract in UTC | S | + | · | + | · | ■ | Open |
| AC-07 | High | R; Schwab dividend handling I | Zero-dividend pricing reaches long-dated calendars and diagonals. On a 1.2% yielder, a 30/365-day call calendar's max profit is overstated 47% and a put calendar understated 28% | `options-scanner/strategy_scanner.py:171` | Carry a per-symbol yield, or a forward implied from put-call parity | M–L | ! | · | ! | · | ■ | **Done** 2026-10-03, `a553038` |
| AC-08 | Medium | R | A zero-bid leg makes a position unmarkable, so a winning spread near expiry gets no mark, no target and no profit lock | `options-scanner/signal_repricer.py:211-214,227-230` | Require `ask > 0` and `bid >= 0` | S | · | · | · | · | ■ | Open |
| AC-09 | Medium | R; frequency I | Schwab's `-999` value is unguarded on gamma and delta. One contract produced a −1.8e12 exposure cell, a delta stop fired, and book net delta read −998.7 | `options-scanner/gamma_tool.py:815-843`; `signal_recommender.py:364-379`; `signal_repricer.py:286-293` | One shared "usable Greek" check at the three sites | S | + | · | + | · | ■ | Open |
| AC-10 | Medium | R; NaN arrival I | NaN clamps to 100 in the Strategy Finder scorer (composite 45.5 becomes 60.0); `implied_vol(NaN)` returns 0.0001 | `options-scanner/strategy_scoring.py:246`; `options_calculator.py:162-178` | Finite checks at the five normalisers and in strike selection | S | + | · | · | + | ■ | Open |
| AC-11 | Medium | R | Finder probability of profit uses a zero-drift normal beyond its valid range (2-year short put 65.7% against 57.0%); same-day structures get a fixed 12-hour sigma | `options-scanner/strategy_scanner.py:282-301` | Lognormal with the row's own expiry and intraday time. Moves tuned gates | M | + | · | · | · | ■ | Open |
| AC-12 | Low | R | Calculator generic probability of profit drops mass outside ±50% of spot (73.9% against 79.1%) | `options_calculator.py:747-765` | Extend the outer segments to zero and infinity | S | · | · | · | · | ■ | Open |
| AC-13 | Medium | R; convert case I | A Rescue roll counts the already-realized loss again in reserved risk ($1,060 against $760). A convert pushes the position toward its own money stop | `services/options_svc/rescue.py:563,711,761`; `options-scanner/paper_adjust.py:343-392` | Book standalone risk on the rolled row; fold the convert credit into `entry_credit` | M | + | · | · | · | ■ | Open |
| AC-14 | Low | C | Commission differs by exit path ($1.30 per contract). `commission.py` keeps its own leg table that counts a short put as two legs | `rescue.py:478`; `services/options_svc/commission.py:57-69` | Use `shared.structures.option_legs`; book the round trip on a Rescue close | S | + | · | + | · | ■ | Open |
| AC-15 | Low | C | Dealer-engine time to expiry is overstated after the close; the last projection column is a floor artefact | `options-scanner/gamma_tool.py:786-790` | Compute through `expiry_time_to_years` | S | + | · | + | · | ■ | Open |
| AC-16 | Low | R | A Ledger row typed `IRON_CONDOR` loses its call side at expiry. Latent: no producer emits that type today | `options-scanner/paper_trader.py:254,325-335` | Canonicalise to `IC` on entry | S | + | · | · | + | ■ | Open |
| AC-17 | Low | I | One 16:00 ET settlement instant is applied to morning-settled index products | Both time helpers | A per-product settlement table | M | · | · | ! | · | ■ | Open |
| AC-18 | Low | C | IV-shock takes time from the snapshot's fetch time while What-if uses now; a floating-point target comparison holds one extra cycle | `options-scanner/options_simulator/engine.py:176-177` | Align the time source; compare with a tolerance | S | · | · | · | · | ■ | Open |
| AC-19 | Medium | R (weekend marks); regular hours I | **Found while fixing AC-07.** Schwab's per-contract `volatility` does not reproduce the contract's own mark under this app's model. Measured 2026-10-03 on XOM, KO, VZ, JNJ, SPY, TSLA, AMZN at 41, 104 and about 350 days: its ratio to the Black-Scholes-Merton volatility the bid/ask midpoint implies ran 0.71 to 1.08 (lowest at one year), and call and put at one strike carry one value. The Finder's later-leg valuation no longer depends on it (AC-07). Still read as a volatility: the Calculator's and Simulator's default IV, the Expected Move cone, the Vol Rank numerator, the scanner's expected-move window | `options-scanner/iv_analysis.py`; `services/options_svc/compute.py` (`calc_iv`, `em_cone`); `options-scanner/strategy_scanner.py` (`extract_options`) | **Measure first, in regular hours**: re-run the two measurement scripts on a trading day. If the gap holds, imply volatility from the mark wherever a price is computed from it | M | + | · | · | · | ■ | Open |

### 4.2 Accuracy — indicators, scoring, regime, factor model, portfolio

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-40 | High | R+M | With every fetch failing, the composite is the string `"0.00"`, which is treated as a real zero: "Strong Bear / Short / 0.70x" is published and pushed. The bridge writes `strong_bearish`, and the scanner then blocks put credit spreads | `sentiment-dashboard/live_composite.py:88-98,334-340`; `services/sentiment_svc/compute.py:1584-1590` | Treat aggregate confidence 0 or total ≤ 0 as absent at both sites; write `unknown` to the bridge; add a test driven from a dead client | S | + | · | + | · | ■ | **Done** 2026-10-03, `7cbdecb` |
| AC-41 | High | R | The Day gauge's session structure and profile are computed on a 10-day frame. A −3% day after nine up days scored +1.00 | `services/sentiment_svc/compute.py:513-533,697-712` | Slice with `_today_session` first; confidence 0 with fewer than six bars | S | + | + | · | · | ■ | **Done** 2026-10-03, `8fed641` |
| AC-42 | High | R+M | The daily frame is weighted 1.0, not 3.0, in EMA alignment: the frame is keyed `"1day"`, the weight table `'daily'` | `compute.py:482,613,629,1308`; `shared/analysis_lib/config.py:35`; `technical.py:407` | Rename the key, or raise on an unknown key | S | + | · | + | · | ■ | **Done** 2026-10-03, `8fed641` |
| AC-43 | High | R+M | Long Term verdict: a price-to-earnings ratio at or below zero scores +60 and a PEG at or below zero scores +40, so a loss-maker gets the maximum valuation score | `trade-analyzer/src/analysis/scoring.py:91-111` | Admit both into valuation only when above zero | S | + | · | · | · | ■ | **Done** 2026-10-03, `65a563b` |
| AC-44 | High | R | Portfolio "vs sector" and "vs SPY" compare different horizons once a position is older than 12 months (+0.889 shown, 0.000 true) | `portfolio-analyzer/src/evaluation.py:23-44,184,295` | Return None when history starts after the entry date, or fetch back to entry | S / M | + | ! | · | · | ■ | **Done** 2026-10-03, `9a51eed` |
| AC-45 | High | R+M | Portfolio relative-strength labels are one horizon too short: the 5, 21 and 63-day figures are labelled 1D, 1W, 1M | `shared/analysis_lib/sector_analysis.py:290-300` | Map the label from the period | S | + | · | · | · | ■ | **Done** 2026-10-03, `f03482c` |
| AC-46 | Medium | R | The return-on-equity percent-or-fraction guess mis-tiers low values: 1.0% scores +60 where the true figure scores −40 | `trade-analyzer/src/analysis/fundamentals.py:72-84` | Divide by 100 unconditionally on the live source | S | + | · | · | · | ■ | Open |
| AC-47 | Medium | R | Three volatility scorers are non-monotone inside their calm bands (198 violations). A test recomputes the same formula and so pins the bug | `sentiment-dashboard/scoring/vix.py`; `tests/test_vix.py:11-28` | Correct the two segment slopes; replace the test with a monotonicity property. The bridge snapshot fixture will move | S | + | · | · | · | ■ | Open |
| AC-48 | Medium | C | The live composite and its history are built by different methods, then differenced for velocity and the "regime break" flag | `live_composite.py:322`; `history_backfill.py:170-176,219` | Score history with the live functions, or compute velocity only over like-for-like snapshots | M | + | · | + | · | ■ | Open |
| AC-49 | Medium | R | `sectors_score` returns 0.0 both for no data and for a real crash day, and those days are then deleted from history. A flat tape scores 4.0 | `sentiment-dashboard/scoring/sector_perf.py`; `history_backfill.py:226-229` | Return None for absence; clamp real scores to 1–10 | S–M | + | · | · | · | ■ | Open |
| AC-50 | Medium | R | Week and Month gauges discount the price sub-score to one-third confidence by construction; a literal 0.0 is passed for VWAP | `services/sentiment_svc/compute.py:1317-1318` | Pass three timeframes for the structural path; renormalise over the terms present | S | + | · | · | · | ■ | Open |
| AC-51 | Medium | R | Short Term fallback verdict has three directional biases: alignment exactly 0 treated as bullish; partial-day volume against full-day averages; a missing VWAP replaced by last close | `trade-analyzer/src/analysis/recommendation.py:105`; `technical.py:247-279`; `services/trade_svc/compute.py:1651` | Direction 0 at alignment 0; time-normalise volume; drop the factor when VWAP is absent | M | + | · | · | · | ■ | Open |
| AC-52 | Medium | R; reach I | One NaN delta silences every big-delta alert for that symbol | `services/options_svc/flow_alerts.py:297-318` | Skip the contract when delta is not a usable number | S | + | · | · | · | ■ | Open |
| AC-53 | Medium | R | The walk-forward has no purge between train and test; on pure noise it shows +0.0038 of information coefficient that is not there | `trade-analyzer/src/analysis/backtest.py:224-233` | Train up to `i - HORIZON`; refit | S + refit | + | · | · | · | ■ | Open |
| AC-54 | Medium | R | Portfolio scorecard: a buy-only average cost overrides the broker's; returns on short positions are sign-inverted | `portfolio-analyzer/src/entries.py:43-45`; `evaluation.py:278,287` | Prefer the broker's cost; multiply by the sign of quantity | S–M | + | · | · | · | ■ | Open |
| AC-55 | Medium | R; prior-close part I | A missing volatility-index day change is passed as 0.0, giving 35.0 at confidence 0.8 where absence should give confidence 0 | `services/sentiment_svc/compute.py:580-594` | Pass None on failure; use `_prior_daily_close` | S | + | · | · | · | ■ | Open |
| AC-56 | Low | R | `growth_quality` always divides by four, so absent inputs drag the score | `recommendation.py:275-280` | Average only the parts present | S | + | · | · | · | ■ | Open |
| AC-57 | Low | R; reach I | Options composite: a NaN factor reads as its maximum; a NaN volatility rank is published as 100, which would also clear the selling floor | `options-scanner/scoring.py:67-78,117-121`; `iv_analysis.py` (rank clamp) | Run every raw input through `_finite_or_none` | S | + | · | · | · | ■ | Open |
| AC-58 | Low | R | The live swing score counts the symbol twice in its own cross-section | `services/trade_svc/swing_model.py:74-97` | Drop the symbol's own row first | S | + | · | · | · | ■ | Open |
| AC-59 | Low | R | Bundle of seven: rotation gauge wording at exactly the trigger; `commit_direction` holds a flipped direction one read; relative-rotation marker area not proportional to weight; advancers and decliners tiles always one colour; RSI of a flat series is 0; business-day count ignores holidays; today's partial bar moves the effort score | `rotation_view.py`; `market_regime.py`; `rrg_view.py`; market dashboard; `technical.py` | Fix individually | S each | + | · | · | · | ■ | Open |
| AC-60 | Medium | R | **Left open by the AC-40 fix.** With every fetch failing the composite's `total_score` is still published as the string `"0.00"`. The band words, the bridge regime and the velocity now read it as absent, but the NUMBER still draws: the Desk's Sentiment chip prints 0.00, the Day arc on `/sentiment` and on the pushed snapshot sits at zero, and the console's score reads 0.00 | `sentiment-dashboard/live_composite.py` (`compute_live`); `services/sentiment_svc/handlers.py` (`_composite_gate`); `webgui/pages/sentiment.py:536,944`; `services/options_svc/market_snapshot.py` | Publish `total_score: None` when `composite_reading` is None, and teach `_composite_gate` and the numeric readers that shape. The gate currently raises on it, which would freeze the cache on the last good reading | S–M | + | · | + | · | ■ | Open |

### 4.3 Security

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SE-01 | High | R+M | The proxy's `/passthrough` route takes `endpoint` from the query string unvalidated and without the shared secret. `/../../trader/v1/...` normalises onto the brokerage account API, reaching accounts, positions, orders and transactions | `schwab-proxy/schwab_proxy.py:707-719,352` | Allow-list the endpoint, or replace the route with the two named routes its callers use; require the secret | S | + | · | + | ■ | · | **Done** 2026-10-03, `88b6ebc`. The passthrough is an allow-list of five market-data endpoints and takes the secret |
| SE-02 | High if unset | C+M; production state I | `require_secret` returns without checking when no secret is configured. `POST /orders/{account_hash}` forwards a real order and nothing calls it | `schwab_proxy.py:438-439,906-916` | Delete the order route. Make the account routes fail closed with no secret | S | + | · | + | ■ | · | **Done** 2026-10-03, `88b6ebc`. The account routes fail closed (503 with no secret configured, 401 on a wrong one); the order route is deleted and `trader_request` refuses anything but GET. **Production has no secret (SV-01): set `PROXY_SHARED_SECRET` before promoting, or the Portfolio page shows the proxy's locked message** |
| SE-03 | Medium | C | `POST /login` parses an unbounded body before any throttle; the edge sets no body limit; the private web app has no memory cap | `webgui/main.py:324-327`; `deploy/caddy/generate_caddyfile.py:380-395` | Body limit at the edge; reject on length before parsing; a memory cap on the unit | S | · | + | · | ■ | + | Open |
| SE-04 | Medium | R | The public process's read-only Redis layer can be absent while the startup check passes: a URL with no user resolves to the default user with the admin password from the environment | `webgui/live_main.py:155-156`; `shared/bus/client.py:138-140` | Remove the admin password from `.env.live`; require a non-default user; at startup attempt a write and refuse to serve unless it is denied | S | + | · | + | ■ | · | Open |
| SE-05 | Medium | R | The Telegram bot token and Discord webhook address are written to logs on connection errors | `shared/notify/channels.py:315,342,368,377,406` | Log the exception type only, as `x_post.py` already does. Rotate if section 5 finds hits | S | + | · | · | ■ | · | Open |
| SE-06 | Medium | R | One address posting every 17 seconds locks the owner out of sign-in indefinitely: every refusal counts toward the global lock | `webgui/login_page.py:300`; `webgui/auth.py:396-398` | Count only attempts that reached the password hash; exempt a valid remember-device cookie | S–M | + | · | · | ■ | · | Open |
| SE-07 | Low | C; mode I | The Schwab token file is written non-atomically with the default file mode | `schwab_proxy.py:225-228` | Temp file, mode 0600, atomic replace. Also closes the token part of AR-09 | S | + | · | + | ■ | · | Open |
| SE-08 | Low | R+M | `shared/proxy_secret.txt` is documented as gitignored and is not | `.gitignore` | Add the pattern | S | · | · | · | ■ | · | **Done** 2026-10-03, `88b6ebc` |
| SE-09 | Medium | I | The public process runs as the same user as every secret with no sandbox, so code execution there is full compromise | `deploy/systemd/generate_units.py:296-334` | Separate user, or `NoNewPrivileges` plus inaccessible paths | M | · | · | ± | ■ | · | Open |
| SE-10 | Low | C; installed grant I | The public Redis user can read every `cache:*` key, including the real portfolio; only page code keeps private views off the public site | `docs/dev-prod-environments.md:232-243` | Grant a finite list of key prefixes, or move private views under a prefix that is not granted | M | ! | · | + | ■ | · | Open |
| SE-11 | Low | C; impact I | Visitor limits key on the full address, so one IPv6 block is unlimited keys; the edge rate limit is off; public scans share the proxy's rate budget with collection | `webgui/visitor_limit.py:30-41`; `config/edge.toml` | Key on the /64; minimum spacing between public scans service-side | S–M | · | + | · | ■ | + | Open |
| SE-12 | Low | C | The proxy's OAuth callback is unauthenticated, uses GET, has no `state`, and puts error text into HTML unescaped; `/health` returns the token file path | `schwab_proxy.py:587-618,518` | Add a `state` nonce; escape; drop the path | S | + | · | · | ■ | · | Open |
| SE-13 | Low | C | Raw report documents served on the private origin interpolate unescaped values and carry no restrictive content-security policy | `services/trade_svc/compute.py:1825-1839`; `deepdive/engine.py:230-235,1436`; `webgui/main.py:495-500` | A sandboxing policy header on those responses; escape; validate the symbol with `clean_symbol` | S | + | · | · | ■ | · | Open |
| SE-14 | Low | R | Six open dependency advisories (`urllib3` ×3, `anyio` ×2, `oauthlib` ×1). No hashes; the audit job is non-blocking; actions pinned by tag | `requirements.lock`; `.github/workflows/ci.yml:89-91` | Bump by hand; make the audit blocking; add a read-only `permissions` block | S | · | · | · | ■ | ! | Open |
| SE-15 | Low | C | Mail `starttls()` is called without certificate verification | `shared/notify/channels.py:418-420` | Pass a default SSL context | S | · | · | · | ■ | · | Open |
| SE-16 | Low | C | Thin security headers at the edge; the static site frames an operator-uploaded report on the same origin | `generate_caddyfile.py:191,328,383-387`; `deploy/site/report.html:102` | Add `nosniff` and a referrer policy; sandbox the frame | S | · | · | · | ■ | · | Open |
| SE-17 | Low | C | Logout is a state-changing GET; sessions cannot be revoked individually (12-hour residual) | `webgui/main.py:367-380` | Make logout a POST with the form token | S | · | · | · | ■ | · | Open |
| SE-18 | Low | C; last item I | Hygiene bundle: desktop notification script built by string concatenation; the gallery capture tool puts an owner-session bootstrap address in a process argument list; root `SECURITY.md` is stale; the calendar key may belong to a broadly privileged default service account | `webgui/main.py:180-185`; `tools/capture_gallery_shots.py:386-409` | Fix individually; use a dedicated service account with no roles | S each | + | · | · | ■ | · | Open |

### 4.4 Architecture

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AR-01 | High | C+M | Promote stops production before the steps that can fail (fetch, pull, install). No trap, no recorded previous commit, no rollback. The post-start check probes only the proxy and the web app | `tools/promote.sh:37,56-66,107-108` | Fetch and verify fast-forward before the stop; trap that restarts on failure; record the previous commit; probe all six service health endpoints | M | · | + | ■ | · | + | **Done** 2026-10-03, `1cd9a2d`. Fetch, fast-forward check and a dry-run of a moved lock run before the stop; the previous commit is recorded; a failure after the stop rolls back and re-probes; all eight processes are probed. The script is tested under bash against a sandbox repository. The first promote that carries it still runs the OLD script |
| AR-02 | High | C+M; race I | Paper-book mutations are not serialized between scheduler and command threads. `_close` is three separate commits. `close_position` has no `status='OPEN'` condition, so a second close overwrites the first | `options-scanner/paper_account_db.py:543-552`; `paper_engine.py:403-414`; `services/options_svc/compute.py:2905-2917` | One re-entrant lock on every book-mutating entry point; add the status condition with a row-count check; one transaction for `_close` | M | + | · | ■ | · | + | **Done** 2026-10-03, `95319de`. One re-entrant lock around every mutation of both paper books; a close is one transaction on a row that is still OPEN, and a second close is refused |
| AR-03 | High | C | Failure detection exists only in an open browser tab. No unit has an on-failure action. Nothing warns before the 7-day Schwab token lapses. A failed backup is silent | `webgui/main.py:2168-2170`; `deploy/systemd/generate_units.py`; `webgui/pages/status.py:263-275` | An on-failure notifier unit on the existing channels; a "token expires within 48 hours" push; a push when the backup fails | M | · | · | ■ | ! | + | **Done** 2026-10-03, `007c63b`. Every generated service carries `OnFailure=` to a notifier; a daily timer warns inside 48 hours of the Schwab sign-in lapsing; a failed backup alerts through the same path. New push category `system` |
| AR-04 | High | C+M | Restore is undocumented and untested. The backup omits the login store, the calendar service-account key, two vendor keys and the site's generated state. Three generations, weekdays only; pruning runs before the failure check | `tools/backup_local.py:69,79,82-103,116-129,354` | Add the files; write a restore section and run it once into a scratch directory; keep a weekly generation; prune only after a clean run | M | · | · | ■ | ! | · | **Done** 2026-10-04, `dbfc3e5`. The missing files and trees are carried, with a test that derives the expected set from `.gitignore`; three dailies plus four weeklies locally and one weekly offsite; pruning follows the result and never removes the newest clean generation; `tools/restore_backup.py` and runbook section 11. The restore runs in the test suite on every run; **it has not been run against a real production generation** |
| AR-05 | Medium | C | Destructive commands are outside both replay gates: `paper_reset`, `paper_close`, `paper_delete`, `paper_delete_closed`, `captured_close`, `set_autoclose` | `services/options_svc/handlers.py:133-134,3255-3336`; `shared/bus/client.py:326` | One age gate for every command in the consumer, with an allow-list of commands safe to run late | S | + | + | ■ | + | + | Open |
| AR-06 | Medium | C+M | `/health` returns `"up": True` unconditionally. Scheduler liveness and tick age are published and read by nothing. The restart budget is a lifetime count | `services/_scaffold.py:190-197,399`; `webgui/pages/status.py:467-472` | Report not-up when the scheduler is dead or its tick is stale; reset the budget after a healthy period; show tick age on the Status card | S–M | · | · | ■ | · | + | Open |
| AR-07 | Medium | C | A restart acks the in-flight command and drops the queued ones into a dead-letter list that nothing reads and nothing trims | `services/_scaffold.py:237-246,267-281`; `shared/bus/client.py:313,332-364` | Show dead-letter length on the Status page; cap the list; publish a "dropped" outcome for user-facing commands | S–M | · | · | ■ | · | · | Open |
| AR-08 | Medium | C | Contracts cover about 16 of 105 cache writes and nothing on read. The money-path views are bare dictionaries. `rescue_apply` trusts the candidate the page echoes back | `services/options_svc/handlers.py:1266,1287,1385,1416,2906-2907,2977` | Small models for the five money-path views; re-derive the Rescue candidate service-side by identifier | M | + | ! | ■ | + | + | Open |
| AR-09 | Medium | C | Single-copy state files are written non-atomically. The portfolio trade store returns empty on corrupt input, and the next sync saves that | `portfolio-analyzer/src/trade_store.py:58-70,83-86`; `webgui/app_settings.py:130` | Temp file and atomic replace; raise or quarantine on corrupt input. (Token file: see SE-07) | S | + | · | ■ | · | + | Open |
| AR-10 | Medium | C+M | The production guard hook and the runbook both target `/home/administrator/prod`, which is not the production checkout | `.claude/hooks/guard_prod_promote.py:46-49,177`; `docs/dev-prod-environments.md:662` | Add the real path, or derive it from a marker; correct the runbook and the hook's message | S | · | · | ■ | + | · | Open |
| AR-12 | Medium | C; persistence I | Redis is the only store for some operator and day state (the auto-close toggle, alert cooldowns, the X daily count), and its persistence mode is not stated. After a flush, consumers fail with "no group" until restarted | `services/options_svc/handlers.py:249,254,549,2401`; `shared/notify/x_post.py:32,264`; `shared/bus/client.py:322-330` | Persist the two toggles on disk; document and assert the persistence mode; recreate the group on that error | S–M | · | · | ■ | · | + | Open |
| AR-14 | Low | C | Schema evolution is ad hoc: no version stamp, ten `ALTER TABLE` statements inside init functions. Of 33 files that open SQLite, 8 set WAL and 2 set a busy timeout. `iv_history.db` has two writing processes and neither | `shared/iv_history.py:112` | One shared `connect()` helper; a version stamp per store | M | + | + | ■ | · | + | Open |
| AR-15 | Low | C | Scheduler slot memory is in-process, so a restart inside a grace window re-fires paid and push slots | `services/options_svc/scheduler.py:280-298,440-456` | Record `(date, slot)` in Redis or a file | S | · | + | ■ | · | · | Open |
| AR-16 | Low | C | The consume loop has no backoff on bus errors: a traceback per stream per iteration while Redis is down | `services/_scaffold.py:284-285` | Log once per outage; capped backoff | S | · | + | ■ | · | · | Open |
| AR-17 | Low | C | A malformed stored envelope makes a `skip_unchanged` view permanently unwritable | `shared/bus/client.py:174-176` | Treat a parse failure of the current value as "changed" | S | · | · | ■ | · | · | Open |
| AR-18 | Low | C | The web tier's import allow-list is prose. One import already contradicts it (`shared.anthropic_counter`, which opens SQLite). No test walks the whole tree | `webgui/pages/settings.py:493` | One test asserting every non-standard-library import root in `webgui/` is in a declared set | S | + | · | ■ | + | · | Open |
| AR-19 | Low | C; effects I | Half-day sessions are unmodelled: on about three days a year collection and scans run three hours past the close and 0-DTE time to expiry is three hours too long | `shared/market_calendar.py` | Derive early closes by rule, as the holidays are | M | · | + | ■ | · | + | Open |

AR-11 (continuous integration gates nothing) is merged into CQ-05. AR-13 (shared
thread pool) is merged into PF-03. AR-20 (`compute.py` as a change hazard) is merged
into CQ-03.

### 4.5 Performance

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PF-01 | Low | R (synthetic) +M; production size I | The proxy parses every Schwab payload and returns a dictionary, so the framework encodes it again. About 0.6 s of processor time for one index-sized chain, during which `/health` and every other call stall **Re-audit:** lowered from High. Synthetic handler time for an index-sized chain fell from 257 ms to 56 ms (store off) and 129 ms (shadow). `/instruments`, `/passthrough` and the account routes still return dictionaries. | `schwab-proxy/schwab_proxy.py:366,625-683` | Return the upstream bytes on the market-data routes | S | + | ■ | · | · | + | Partly: four market-data routes return bytes |
| PF-02 | High | I (arithmetic) | The 1-minute collection poll has no headroom on the shared 5 requests per second at quarter-hours; the limiter has no priority. Twice the watchlist cannot fit **Re-audit:** unchanged as shipped. The collector tail exists and is off; at interval 3 the same third of tail symbols is always due on the quarter-hour, so the worst minute gets the least relief. | `schwab_proxy.py:125,320-337`; `options-scanner/gex_collector.py:67`; `services/options_svc/scheduler.py:501-504` | **Measure first** (count the skip warnings). Then a priority lane in the limiter, and a slower cadence for the long tail | M | · | ■ | ! | · | ± | **Done** 2026-10-04, `427ae50`, `dfe84d1`, after measuring (SV-10). The limiter has a priority lane and the collection poll uses it: 4 of every 5 calls while it is fetching. The slower cadence for watchlist-only symbols already exists and stays the operator's switch (`[collection] tail_interval_min`). **Re-count SV-10 after the first full session on the new code** |
| PF-03 | Medium | C+M; magnitude I | Six blocking stream pollers, every scheduler branch and every command handler share one default thread pool. With eight long jobs no stream is read | `services/_scaffold.py:254-264,311`; `services/options_svc/app.py:38-54` | A dedicated thread per consumer loop; a separate bounded pool for the rest | S | · | ■ | + | + | · | Open |
| PF-04 | Medium | C | `cmd:options` is one serial queue. A manual rescan or a Finder scan holds up reprice, paper and rescue commands; a `paper_create` queued more than 180 seconds is refused as stale | `services/options_svc/handlers.py:59,3233-3533` | Fast lane for math and local-database commands, slow lane for anything that fetches. **Requires AR-02 first** | M | ! | ■ | ! | · | + | Open |
| PF-05 | Medium | C; size I | The Market Scanner sends the whole day's union (about 4.5 MB by the close) to the browser on every repaint and every 5-minute re-stamp | `webgui/pages/options/scanner.py:656-712,1062-1081,1291-1307` | Server-side paging, or send only changed columns | M | ! | ■ | · | · | · | Open |
| PF-06 | Medium | C; volume I | Append-only history keys are rewritten in full, and read back in full for comparison, every minute | `services/options_svc/handlers.py:1714-1722`; `shared/bus/client.py:174-199` | Small: skip the comparison during collection. Large: an append structure | S / L | ! | ■ | ! | · | ! | Open |
| PF-07 | Medium | R (synthetic) | Each browser tab parses and holds its own copy of large payloads (1.4 MB of JSON becomes 4.3 MB in memory). Fifty public visitors approach the memory limit | `webgui/pages/options/gamma.py:3034,3063`; `webgui/bus_client.py:86-93` | A process-wide `(view, version)` cache so tabs share one parse | S–M | ! | ■ | · | + | · | Open |
| PF-08 | Medium | R (synthetic) +M | `signal_marks` grows without bound and its latest-mark query has no composite index: 280 ms on 640,000 rows against 0.1 ms with one | `options-scanner/signal_db.py:45-62,336-362` | Add an index on `(signal_id, mark_ts)`; batch a cycle's marks in one transaction | S | · | ■ | · | · | · | Open |
| PF-09 | Low | R (synthetic) | Every 15-minute scan rewrites a year of realized-volatility rows per symbol (2.65 s, about 18,500 unchanged rows) | `options-scanner/scanner_engine.py:1754-1760`; `shared/iv_history.py:424-479` | Once per session per symbol; drop two redundant indexes | S | + | ■ | · | · | · | Open |
| PF-10 | Low | I | The autoscan re-fetches a year of daily history and a volatility chain per symbol every slot: half of each scan's burst **Re-audit:** unchanged as shipped. The bar store and the wide fetch address it once switched on. | `scanner_engine.py:168-174,1954-1958` | A time-to-live on history; refresh the volatility chain every second or third slot. Measure score drift first | S–M | · | ■ | · | · | ! | Open |
| PF-11 | Low | C | The Redis client has no socket timeout; version probes run on the event loop, so a Redis stall freezes every tab silently | `shared/bus/client.py:133-135` | Timeouts on the web tier's client only, not on the blocking stream read | S | · | ■ | · | + | · | Open |
| PF-12 | Low | C | Calculator, Simulator, Rescue and Sentiment still use separate uncoalesced one-second version probes and some on-loop reads | `calculator.py:1821-1823`; `simulator.py:1115-1119`; `rescue.py:1318-1321`; `sentiment.py:1067-1075` | One batched probe per page; reads off the loop | S per page | + | ■ | · | · | · | Open |
| PF-13 | Low | I | A restart fires rescan, collection and manage together and demands roughly twice the per-minute budget. No stop timeout is set on the units | `services/options_svc/scheduler.py:57-63,553-666` | Persist last-run slots (with AR-15); stagger; set an explicit stop timeout | S–M | · | ■ | · | · | + | Open |
| PF-14 | Low | I | The daily history purge runs inside the first collection tick of the day | `services/options_svc/compute.py:4941`; `options-scanner/gex_history_db.py:957-978` | Move it to the post-close window | S | · | ■ | · | · | · | Open |
| PF-15 | Low | R (synthetic) | The chain grid renders every strike of an expiry as one block (675 KB for 500 strikes) | `webgui/pages/options/entry_panel.py:311`; `chain_grid.py:309-342` | A window around spot with a "show all" control | S | · | ■ | · | · | · | Open |

### 4.6 Code quality

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CQ-01 | High | R+M | `_rescue_regime()` calls `evaluate_regime`, which is not defined at module level. The error is swallowed, so the Rescue regime modifier has been dead since the import was removed. All seven tests replace the function with a stub | `services/options_svc/compute.py:9523,9528` | Import inside the function; one test that calls the real function; report the degrade | S | ■ | · | · | · | + | **Done** 2026-10-03, `ade9c87` |
| CQ-02 | High | R+M; CI state I | The lint gate exists in three places and is enforced in none: `ruff check .` fails on `main`; no git hook is installed; the editor hook discards unfixable findings and does nothing in a worktree **Re-audit:** `ruff check .` passes on `ad873ec`; enforcement unchanged. | `pyproject.toml`; `.pre-commit-config.yaml`; `.claude/hooks/ruff_fix.py:36-53` | Install the pre-commit hook; make the editor hook print unfixable findings; look at the Actions tab | S | ■ | · | · | + | + | **Done** 2026-10-03, `9e3b700`. A commit hook (`core.hooksPath`), a test that runs the gate over the tree, and an editor hook that reports what it could not fix and works in a worktree. CI was red on `main` for an unrelated reason, five Linux-only import-probe tests, fixed in the same commit (SV-16) |
| CQ-03 | High | R | `options_svc/compute.py` is 10,548 lines and growing about 1,000 a month: 278 functions, 199 function-local imports, 193 broad exception handlers. `handle_command` is a 370-line chain. Tests patch the module by name at 495 sites **Re-audit:** now 10,668 lines; `schwab_proxy.py` 1,819 → 2,018. `collection_tiers` (about 100 pure lines) was added here and could live in its own module. | `services/options_svc/compute.py`; `handlers.py:3164` | Split by domain behind stable public functions; a dispatch table. Do after clusters B, C, D | L | ■ | · | + | + | ! | **In progress** 2026-10-04, `86e65d1`. Done: `handle_command` is a table of 41 registered functions, and the replay guard is applied by the dispatcher (it was missing on `calc_rate`); the collector's tier logic is in its own module; `test_compute_module_shape.py` holds a line ceiling on `compute.py` that can only be lowered (10,726 → 10,638). Left: the split by domain, about 10,600 lines and 495 patch sites |
| CQ-04 | High | R | Page `render()` functions have doubled or tripled: `gamma` 1,484 lines with 62 nested functions, `desk` 1,057, `calculator` 1,027 | `webgui/pages/options/gamma.py:2169`; `desk.py:3183`; `calculator.py:968` | Extract a builder per panel, starting with gamma and calculator | L | ■ | · | + | ! | · | **In progress** 2026-10-04, `3ac2339`. Done: the Desk's eleven row builders and click-throughs are module-level (render 1,057 → 781 lines, 32 → 22 nested functions); `test_render_size.py` holds a ceiling on all three functions that can only be lowered. Left: `gamma.render` (1,484 / 62) and `calculator.render` (1,027 / 40), which need a page run in the browser harness to verify, not only tests |
| CQ-05 | Medium | C | Continuous integration omits `services/news_svc`, `shared/notify/tests`, `tests/`, `deploy/` and the hook tests; the options-scanner suite is non-blocking and still deselects tests that now pass; no type-check job. Promote consults no check **Re-audit:** unchanged; the three new collector and scanner test files (740 cases) run in the non-blocking lane. | `.github/workflows/ci.yml:136-207` | Add the rows; drop the soft flag and deselects; add the type check | S | ■ | · | + | + | + | Open |
| CQ-06 | Medium | R | The silent-degrade guard matches the word "log." in source text, so two handlers around paid Claude calls pass because their error page mentions "the service log." The guard covers `services/` only | `services/tests/test_no_silent_degrades.py:33,56-58`; `compute.py:7150,7641` | Match call nodes, not substrings; report the degrade in both handlers; extend to `options-scanner/` and `shared/` | S | ■ | · | · | + | + | Open |
| CQ-07 | Medium | R | 31 `_num` helpers with 16 different behaviours (nine pass NaN through); 20 `_finite` helpers with five **Re-audit:** five more private helpers in the new modules, all strict (they reject NaN, infinity and booleans). | Across `services/`, `webgui/pages/`, `shared/` | One `shared/numeric.py` with explicitly named variants, migrated one behaviour group at a time. Never by search-and-replace | M | ■ | · | · | + | ± | Open |
| CQ-08 | Medium | R | Lint and type coverage exclude the four engine folders as "copied verbatim", though they are the most actively changed and already pass the configured rules. A quarter of functions carry any annotation **Re-audit:** `market_store.py`, `marketdata_config.py` and `chain_carry.py` are outside type-check scope. | `pyproject.toml`; `pyrightconfig.json` | Delete the four excludes; then adopt the unused-import and redefinition rules | S | ■ | · | · | · | + | Open |
| CQ-09 | Medium | R | `CLAUDE.md` is about 300 KB against its own ~100 KB target, with stale present-tense claims and internal contradictions (test counts, the `_clamp` duplication, the web tier's "zero sqlite3") **Re-audit:** now about 311 KB; the new text includes dated figures that will go stale. | `CLAUDE.md` | Repeat the 2026-08-16 relocation; add a size test | M | ■ | · | · | · | + | Open |
| CQ-10 | Medium | R | "Configurable by default" is not applied to touched code: 393 module-level numeric literals, including trade-selection thresholds edited this week | `options-scanner/scanner_engine.py:295,300,323`; `services/options_svc/hiro.py:226,269`; `matrix.py` | Move trade-selection thresholds first, each with a reload test | M | ■ | · | + | · | ! | Open |
| CQ-11 | Low | R | Guard tests narrower than their names: the lazy-import test checks that modules exist, not that names are bound; the inline-style guard is a hand-kept list missing 21 pages; the standard-library shadow guard was deleted and not replaced | `services/options_svc/tests/test_lazy_imports_resolve.py`; `webgui/tests/test_no_inline_style.py` | Glob-based sweeps; re-home the shadow guard | S | ■ | · | · | · | + | Open |
| CQ-12 | Low | R | One stale cadence in the hover help (news poll says 5 minutes; config says 2) | `webgui/page_help.py:916`; `config/news.toml:6` | Correct it | S | ■ | · | · | · | · | Open |
| CQ-13 | Low | R | The phone-image console mirrors eight web-tier functions by comment, not by test | `services/options_svc/market_console.py` | A body-equality test over the named pairs | S | ■ | · | · | · | + | Open |
| CQ-14 | Low | R | Largest live module with no test: `history_backfill.py` (486 lines) | `sentiment-dashboard/history_backfill.py` | Add tests; AC-48 and AC-49 are in this file | M | ■ | · | · | · | + | Open |
| CQ-15 | Low | R | Stale dependency notes; two imports declared nowhere (`bs4`, `cryptography`) | `requirements.txt`; `requirements-dev.txt` | Tidy | S | ■ | · | · | + | · | Open |
| CQ-16 | Low | R | Three markers describing real limits: multi-leg transactions parsed as their first leg only; futures multiplier fixed at 1 | `schwab-proxy/schwab_proxy.py:878`; `portfolio-analyzer/src/live.py:25` | Fix or record as accepted | S–M | ■ | · | · | · | + | Open |

### 4.7 Carried forward from earlier audits (still open, not counted above)

| ID | Finding | Status |
|---|---|---|
| AC-90 | The `$VIX1D` session latch beats the daily close, inflating the spike percentage. Tracked by a strict expected-failure test | Open |
| AC-91 | Sector & Industry and Sector Rotation can print opposite regime verdicts; they read different quantities on different scales | Open (product decision) |
| AC-92 | Factor model C12: univariate weighting double-counts the momentum cluster | Open (needs a refit) |
| AC-93 | Factor model C13: the `low_vol` sign is regime-overfit | Open (needs a refit) |

### 4.8 Re-audit findings — the market-data store, collector tiers, chain carry, wide fetch

Added 2026-10-03 for commits `c89b1d5..ad873ec`. The tag at the start of each finding
says when it bites:

- **[Live]** — in the shipped configuration, today.
- **[On: …]** — only after the named setting is switched on (store chains, quotes or
  bars; the collector `tail` above 1; the scanner `wide` fetch).
- **[Shadow]** — it would mislead the decision to switch on, because shadow mode
  cannot see it or counts it wrongly.

Numerical results here come from a fake Schwab, a controlled clock and synthetic
chains. None was checked against real Schwab responses.

**Accuracy**

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-100 | High | R+M | **[Shadow]** Shadow's daily-bar verdict skips the values of the last, still-moving bar — the only bar that can be stale. In-session repeats record "match" while the stored close differs (reproduced 501.50 against 501.97) | `schwab-proxy/market_store.py:575-576,1153-1156` | Record a separate outcome for the moving bar: stored against fresh close, with the entry's age | S | · | · | + | · | ■ | **Done** 2026-10-03, `ef67cf6` |
| AC-101 | Medium | R+M | **[On: bars]** A bar fetched before the close is served as the day's bar until ten minutes after it, and `/pricehistory` accepts no age override. Reproduced: fetched 14:45, served 15:05 with 506.25 against a 506.50 close. Readers: the 15:00 scan's technicals, sentiment trends, and public trade-idea settlement, where the result is permanent on the site | `market_store.py:414-429,1095-1098`; `schwab_proxy.py:753-775`; `services/options_svc/compute.py:3799-3812` | End the "live" period at the regular close; accept `maxAge` on `/pricehistory`; settlement callers send 0 | S–M | · | ! | + | · | ■ | Open |
| AC-102 | Medium | R; size I | **[Shadow]** Shadow counts the collector's own in-session repeats as savings that "on" will not deliver, because the collector sends its age limit only when its own config file says "on". The detail table has no time column, so the documented "subtract about 700" cannot be checked. A dev checkout borrowing an "on" proxy would be handed its own previous chain | `services/options_svc/compute.py:4946-4948`; `market_store.py:636-648` | Always send `fresh_max_age_sec`; it is ignored when the store is off and makes shadow exact | S | + | · | + | · | ■ | Open |
| AC-103 | Medium | R | **[Shadow]** The chain verdict compares only which contracts are present (side, date, strike). A cut that differs in contract count, underlying price or the day-count in every key still records a match, and a mismatch log line does not say what differed | `market_store.py:199-215,920-930` | Add the count, the full expiration keys and the non-moving header fields to the verdict; log the first difference | S | + | · | · | · | ■ | Open |
| AC-104 | Medium | R | **[On: store]** Config age limits have no ceiling; only the caller's own `maxAge` is capped. A Settings edit can make the store serve a 20-hour-old chain or a 6-hour-old quote to every caller that sends no limit | `shared/marketdata_config.py:55-71`; `market_store.py:641-648` | Clamp in the loader (for example chains 300 s open and 3,600 s closed, quotes 60 s) | S | · | · | + | + | ■ | Open |
| AC-120 | Medium | R (synthetic) +M | **[On: tail]** The carry's gamma cap and tiny-gamma floor bias net exposure against the move: growth is capped, shrinkage is not. In the last 10–15 minutes of a symbol's expiry day, or after a jump, carried net GEX can change sign and the flip level and walls move. Outside that the carry is exact to rounding | `options-scanner/chain_carry.py:34,114-116` | When the cap binds for a symbol, refetch it in the same poll instead of writing the capped chain | S–M | · | ! | · | · | ■ | Open |
| AC-121 | Medium | C | **[On: tail]** A carried row cannot be told from a fetched row in storage or on screen. At interval 3, two of every three rows for a tail symbol hold modelled Greeks and repeated volume and premium; later studies would read them as observations | `options-scanner/gex_history_db.py:481` | A nullable `carried_age_sec` column; research tools filter on it | S–M | · | · | + | · | ■ | Open |
| AC-140 | Medium | R | **[On: chains]** Rescue's stale-price guard re-prices from the same stored chain the candidate was priced from. Within 45 seconds the drift is exactly zero, so the 15% guard cannot fire; after the close the chain can be 30 minutes old. The apply then books at that price | `services/options_svc/compute.py:9540-9556`; `handlers.py:2908-2909`; `options-scanner/paper_adjust.py:524-537` | The apply path sends `maxAge=0` | S | · | ! | + | · | ■ | Open |
| AC-141 | Medium | C+M | **[On: chains]** Paper fills are priced from a stored chain up to 45 seconds old, often the same snapshot the signal was built on, so the book records no movement between signal and entry. The repricer's fetch sends no age limit | `options-scanner/signal_repricer.py:180-201`; `paper_broker.py:107-122` | Give `_fetch_chain` an age argument; entries and closes send 0; marks state 45 explicitly | S | + | ! | · | · | ■ | Open |
| AC-106 | Low | R; Schwab side I | **[On: chains]** A chain entry survives midnight Eastern inside the 30-minute closed limit, so its day-count keys are one day stale | `market_store.py:268-272` | Stamp the entry with the Eastern date and refuse across it | S | · | · | · | · | ■ | Open |
| AC-107 | Low | R; half-day part I | **[On: bars, quote mode]** In quote-built mode the answer steps backward at the close; half days are not modelled | `market_store.py:1068-1069,1095-1098` | Same boundary fix as AC-101; half days are AR-19 | S | · | · | · | · | ■ | Open |
| AC-108 | Low | R; Schwab side I | **[On: quotes]** A quote request whose only uncached symbol is invalid may fail as a whole although the held symbols are fresh | `market_store.py:1029` | On an upstream error with held symbols, retry the full list once | S | · | · | · | · | ■ | Open |
| AC-122 | Low | C | **[Live]** The restored Rescue tilt ignores trend confidence (the scan gate requires 0.6) and treats an iron condor as put-side only | `services/options_svc/rescue.py:145-150` | Require the same confidence floor; tilt a condor on its tested side | S | · | · | · | · | ■ | Open |
| AC-123 | Low | I | **[On: chains]** The collector's fresh-age ceiling (30 s) equals the scheduler's tick spacing, so at 28–30 a one-minute symbol can be answered with the previous poll's chain. Even at the shipped 20, real samples can be 40–80 seconds apart, which the per-minute volume detectors do not expect | `options-scanner/gex_collector.py:303,367-376`; `services/options_svc/scheduler.py:481` | Lower the ceiling to about 20–25; pass the answer's age to the detectors | S | · | ! | · | · | ■ | Open |
| AC-124 | Low | I | **[On: wide]** A wide answer is accepted if it lists any expiration, so a partial one cannot be detected; a symbol whose wide fetch times out pays a 30-second stall plus three fetches on every scan with no memory of the failure | `options-scanner/scanner_engine.py:287`; `iv_analysis.py:388-395` | Skip the wide fetch for a symbol after N consecutive fallbacks; log an empty far window | S | · | + | · | · | ■ | Open |
| AC-125 | Low | R | **[On: tail]** Intervals 2 and 4 are accepted although the flow-acceleration window needs a divisor of 15 (the ratio wobbles 0.81–1.08 at 4 against thresholds of 0.6 and 1.5) | `services/options_svc/compute.py:4951` | Read 2 as 3 and 4 as 5, or reject them | S | · | · | · | · | ■ | Open |
| AC-126 | Low | R (synthetic) | **[On: tail]** The carry prices at the poll-start clock while the quote is read up to 30 seconds later (per-strike error 10% at 14:55 on expiry day; net under 1.1%) | `options-scanner/gex_collector.py:483` | Read the clock at carry time | S | · | · | · | · | ■ | Open |
| AC-127 | Low | R | **[Live, fallback collector only]** The fallback bridge writer still emits the old trend vocabulary, which the exact-match Rescue tilt and the scan gate never act on | `sentiment-dashboard/live_composite.py` (`publish_bridge`) | Delete the fallback writer or map its states | S | + | · | · | · | ■ | Open |
| AC-143 | Low | C | **[On: chains]** A user-pressed Load or Refresh returns stored data with a new "Updated" stamp; only the collector reads the answer's age | `services/options_svc/compute.py:3848-3856,8179-8182,8250-8252`; `schwab-proxy/proxy_client.py:302-311` | User commands send a small limit; carry the answer's age into the published payload | S–M | · | ! | + | · | ■ | Open |

**Architecture, performance, security, code quality**

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AR-100 | Medium | R | **[On: chains]** A request that just misses is widened to the week fetch, and if that call fails the caller gets the failure with no retry of the request as asked. Several different narrow requests waiting on one week fetch all receive the one error. None of this path runs in shadow, so its first exposure is the switch | `schwab-proxy/market_store.py:798-824,944-977` | On an upstream error from a widened fetch, fetch the request as asked once before raising | S | · | ! | ■ | · | + | Open |
| AR-101 | Low | R+M | **[Live]** A missing or half-saved `marketdata.toml` runs `shadow`, not `off`: the built-in default mode is `shadow`. An operator who switches off in an incident and makes a syntax error keeps the store on the request path. It never answers locally | `shared/marketdata_config.py:18-19`; `shared/config_toml.py:148-153` | Default to `off`, or keep the last good config on a parse failure | S | · | · | ■ | · | · | Open |
| AR-102 | Low | R | **[On: quotes]** Quote blocks are served by reference; a miss reports an age of 0 although the fetch began earlier | `market_store.py:355-357,1028` | Copy on serve, or document the invariant; report the true age | S | · | · | ■ | · | · | Open |
| AR-122 | Low | C | **[On: wide]** Two implementations cut a chain to a date window (the proxy's and the scanner's); their parity rests on comments | `market_store.py` (`_render`); `options-scanner/scanner_engine.py` (`slice_chain`) | One test feeding the same chain through both | S | + | · | ■ | · | + | Open |
| PF-100 | Medium | I | **[On: bars, ttl mode]** The quarter-hour burst is relieved only on alternate slots: by the design's own call mix, about 337 then 257 calls against 300 a minute, because the year series expires in step with every second scan | `market_store.py:1048-1053` | Spread the limit per symbol, or move to quote-built bars once shadow supports it | S | · | ■ | · | + | ! | Open |
| PF-101 | Low | R (synthetic) | **[Live]** The chain store is bounded by entry count, not bytes (worst case about 360 MB; expected 25–60 MB); eviction is oldest-stored; the proxy unit has no memory cap | `market_store.py:226-265`; `deploy/systemd/generate_units.py:182-183` | A byte budget; drop entries whose window has ended; measure before capping | S | · | ■ | · | + | · | Open |
| PF-102 | Low | R (synthetic) | **[Live]** Shadow re-parses the stored chain on every request, and a miss encodes twice (about 73 ms on an index-sized chain). Still half the cost before the change | `market_store.py:908-938`; `schwab_proxy.py:700-702` | Keep the comparison shape on the entry at store time | S | · | ■ | · | · | · | Open |
| PF-103 | Low | R | **[On: chains]** The widened refetch ignores age and session: a one-expiration request after hours becomes the whole week (about seven times the bytes for an index) | `market_store.py:301-332` | Skip widening while every session is closed or the held entry is old | S | · | ■ | · | · | · | Open |
| PF-104 | Low | C | **[Live]** Attribution gaps in the new per-caller counts: `/passthrough`, `/instruments` and the tracker are uncounted; five callers are `unknown`; public Finder, Rescue and tool work is counted as `options_svc`, so the public share of the rate budget cannot be read | `schwab_proxy.py:777-810,1195` | Caller labels on the bare sessions; a per-call label for the public workers; named routes in place of `/passthrough` (also closes SE-01) | S | + | ■ | + | + | · | Open |
| SE-100 | Medium | C+M | **[Live, pre-existing]** `/track` and `/untrack` are unauthenticated. A local or tailnet caller can replace a real tracked trade's strikes (false target and stop events in the analytics store) or subscribe invented trades. The paper book itself is not affected | `schwab_proxy.py:1139-1310`; `trade_registry.py:230-232` | Put both routes behind the shared secret; refuse an id that is not open in the Ledger | S | · | + | · | ■ | + | Open |
| SE-101 | Low | I | **[On: store]** Caller-chosen symbol strings become per-day lock and failure keys with no bound | `market_store.py:84-93,677-688,814-822` | Validate the symbol with `clean_symbol` in the four handlers; cap the two maps | S | + | · | · | ■ | · | Open |
| SE-102 | Low | C | **[Live]** Every store fault logs a full traceback with no once-only guard; a systematic fault is about 50,000 a day | `market_store.py:831-839` | Warn once per area and exception type | S | + | · | · | ■ | · | Open |
| CQ-100 | Medium | C | **[Live]** Store faults and every shadow verdict exist only on the proxy's `/stats/api_calls`, for today only. The Settings card shows one number and prints a counter failure as a real zero. The design says the fault count is on `/health`; it is not | `schwab_proxy.py:510-514`; `webgui/pages/settings.py:96-99`; `schwab-proxy/api_call_counter.py:166` | Put the mode and fault total on proxy `/health` and the Status card; allow a past day; return "unknown" on counter failure | S–M | ■ | · | + | · | + | Open |
| CQ-103 | Low | C | **[Live]** Literals left in new code: the bar-comparison tolerances that decide the quote-mode verdict; the collector's 7-day window, tied to `wide_refetch_max_days` by comment only; the tracker's target and stop fractions mirroring `trade_mgmt.toml` by comment | `market_store.py:463,489-490`; `gex_collector.py:506`; `schwab_proxy.py:1090-1091` | One test tying the window to the config default; tolerances into `[bars]` | S | ■ | · | + | · | ! | Open |
| CQ-105 | Low | C | **[Live]** No captured Schwab payload in the new tests: every chain, quote and bar series is hand-built. The cut, the quote-built bar and both re-audit reproductions rest on a model of Schwab's behaviour | `schwab-proxy/tests/` | One scrubbed real chain, an equity and an index quote, one daily series; a cut-against-narrow-fetch test | S–M | ■ | · | + | · | + | Open |

---

## 5. Facts to confirm on the server

These convert inferred findings into measured ones. All are read-only. Run from the
production checkout. Record the result and date in the last column.

| ID | Settles | Command | Expect | Result |
|---|---|---|---|---|
| SV-01 | SE-02 | `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/accounts` | `401` | 2026-10-03, before the fix: **`200`** — production has no `PROXY_SHARED_SECRET`, so the account routes were open to anything that could reach the port. After `88b6ebc` the same request answers `503` until a secret is set, then `401` |
| SV-02 | SE-01, SE-12 | `tailscale serve status ; tailscale funnel status ; tailscale status` | Funnel off; every listed device can reach the proxy | |
| SV-03 | SE-07 | `stat -c '%a %n' schwab-proxy/proxy_tokens.json shared/tokens.json shared/appsettings.json shared/webgui_auth.json shared/notifications.json shared/google_calendar_sa.json .env .env.live` | `600` on all | |
| SV-04 | SE-04 | `grep -c '^MEMURAI_PASSWORD=' .env.live` and inspect the user in `REDIS_LIVE_URL` (do not print the password) | `0`, and a non-default user | |
| SV-05 | SE-10, AR-12 | `redis-cli ACL GETUSER live` ; `redis-cli CONFIG GET save` ; `redis-cli CONFIG GET appendonly` | The grant as documented; a stated persistence mode | |
| SV-06 | Network | `ss -ltnp \| grep -E ':(6379\|8100\|8500\|8501\|82[0-9]{2})\b'` | Every listener on `127.0.0.1` | |
| SV-07 | SE-05 | `grep -rlE '/bot[0-9]+:\|api/webhooks/[0-9]+/' logs/ \| head` | No files. Any hit means rotate | |
| SV-08 | SE-03 | `systemctl --user show trading-prod-webgui -p MemoryMax` | Currently unlimited | |
| SV-09 | PF-03 | `nproc` | Sets the thread-pool size (`min(32, cores + 4)`) | 2026-10-03: **4** |
| SV-10 | PF-02 | `journalctl --user -u trading-prod-options_svc --since today \| grep -c "still running; skipping"` | The count of lost collection slots per day | 2026-10-02: **6**. Four sessions 09-29 to 10-02: **22**, of which **20** at :01, :16, :31 or :46 — the minute after a quarter-hour scan started — one at 08:53 (the Income scan runs at 08:52) and one at 14:18. The audit's mechanism holds |
| SV-11 | PF-08 | `sqlite3 options-scanner/data/signals.db "select count(*) from signal_marks"` | Row count | |
| SV-12 | AC-01 | Count rows in `signal_outcomes` with reason `EXPIRED` whose close time is before 15:00 CT on the expiration date | The number of contaminated outcomes | 2026-10-03: 930 outcomes, 307 `EXPIRED`. **172** closed on the expiry day, every one before 15:00 CT. The other **135** closed on a later day, against that day's mark or the entry price. So all 307 (33% of outcomes) were settled at the wrong time or price |
| SV-13 | AC-04 | Count condor rows in `signals` where the call-wing width differs from the put-wing width | Zero would make AC-04 latent | 2026-10-03: 10 condors captured, **4** with unequal wings, **2** with the call wing wider (IBKR 1/3, DELL 2.5/5). Stored `width` was the put wing on all four. The paper Account holds 2 condors, both equal-winged. Real, rare, never reached the book |
| SV-14 | AC-03 | Count open rows in `paper_positions` with strategy `SHORT_PUT`, `NAKED_PUT` or `COVERED_CALL`, and rejected orders against them | The positions that cannot close | 2026-10-03: **none, ever** — no single-leg position has been opened in the paper Account and there is no rejected close order. AC-03 was latent in production; the fix removes it before the first income position is opened |
| SV-15 | SE-14 | `.venv/bin/python -m pip_audit` | The installed environment, not only the lock | |
| SV-16 | CQ-02, CQ-05 | The repository's Actions tab on GitHub | Whether the lint job is red or not running | 2026-10-03 (`gh run list`): the workflow was **red on every recent push to `main`**. Lint was passing; the failures were five import-probe tests that fail only on Linux (`_sysconfigdata*` is imported there). Fixed in `9e3b700`. The `audit` job reports six advisories and is non-blocking (SE-14) |
| SV-17 | PF-01 | `py-spy top --pid $(systemctl --user show -p MainPID --value trading-prod-proxy)` during market hours | After the re-audit change: `json.loads` and `dumps`, no longer `jsonable_encoder` | |
| SV-18 | AC-100, AC-102, AC-103, CQ-100 | `curl -s http://127.0.0.1:8100/stats/api_calls \| python3 -m json.tool` | Today's shadow outcomes; `store_degrades` should be empty | |
| SV-19 | AC-102, PF-104 | `sqlite3 schwab-proxy/data/api_call_counts.db "select endpoint,caller,outcome,sum(n) from api_calls_detail where day=date('now','localtime') group by 1,2,3 order by 4 desc"` | Per-caller outcomes; how much is `unknown` | |
| SV-20 | PF-101 | `systemctl --user show trading-prod-proxy -p MemoryCurrent -p MemoryPeak -p MemoryMax` | The store's real footprint (expected 25–60 MB above the old baseline) | |
| SV-21 | AC-101 | `journalctl --user -u trading-prod-proxy --since today \| grep "stored daily series"` | Any "last bar" difference after 15:10 CT means Schwab revises the settled bar | |
| SV-22 | AC-120 | `tools/measure_chain_carry.py` on an expiration Friday, 14:30–15:00 CT | How often the gamma cap binds on real chains | |
| SV-23 | SE-102 | `journalctl --user -u trading-prod-proxy --since today \| grep -c "market store degraded"` | `0` | |
| SV-24 | AC-02 | Count `EXPIRED` rows in `paper_positions` by whether the exit date is the expiration date | Positions settled on a later day | 2026-10-03: 6 expired positions; **2** closed on the expiry day (the manual manage button, after the close) and **4** on a later day, against that day's quote |
| SV-25 | AC-07, AC-19 | For seven names, at-the-money call and put at about one year: the chain header's `dividendYield`, the yield put-call parity implies, and the contract `volatility` against the volatility its own mark implies | Whether the chain carries a usable yield, and whether its IV prices its own mark | 2026-10-03 (a Saturday, Friday's closing marks). Header yield against parity: XOM 2.51 / 2.88, KO 2.48 / 2.57, VZ 6.16 / 6.25, JNJ 2.09 / 1.83, SPY 0.98 / 0.80, TSLA 0 / −0.28, AMZN 0 / −0.10. Contract IV over mark-implied IV: 0.71–0.88 at one year, 0.81–1.08 at 41–104 days. **Repeat in regular hours** |

---

## 6. Verified sound — do not re-flag

Each of these was checked and held.

**Accuracy.** Black-Scholes price (put-call parity residual 3.6e-15). Every Greek
against finite differences, including charm and vanna. Both time helpers on expiry day
at 14:59, 15:00 and 15:01 CT. The gamma-exposure unit, to the cent. `payoff_metrics`
for eleven structures including commission, and its two valuation paths agreeing.
`book_caps` against brute force in 20,000 random cases. The buying-power cash identity
through a roll, a convert and an assignment. Ledger exits at quantity 3 booking the
same number by manual close and expiry. Position-Greek signs for every structure. The
profit-lock ladder never lowering a stop. EMA, Wilder RSI and ADX, MACD, session VWAP
and value area against independent implementations. The regime simplex summing to 1.0
under every bad-input perturbation. The live factor score matching the fit to 1e-16.

**Security.** A pure-ASGI default-deny gate covering HTTP and websocket with exactly
two open paths. Token kinds that are not interchangeable. Second-factor replay refused
and persisted before success. Public write paths that rebuild every field from an
allow-list and refused NaN, infinity, booleans and key-injection characters under
adversarial runs. No shell execution anywhere in services or the web app. No SQL built
from external input. All 22 `ui.html` sites sanitised. Git history clean across 2,991
commits for the patterns scanned. Backups encrypted before upload.

**Architecture.** No service imports another service. The public entrypoint cannot
import the private one unnoticed. Poison stream entries are dead-lettered without
failing the batch. Every `paper_create` outcome is published. The process refuses to
start off Central time. Lock ordering is clean outside the paper books.

**Performance.** Version-gated polling with cheap side keys. Large reads off the event
loop. A slow scheduler branch can only delay itself. The rate limiter is correct under
concurrency. Service-side caches are bounded. Public pages poll only while a request
is pending.

**Code quality.** About 14,200 test functions; test code exceeds source code. The type
check is clean on its configured scope. The lock file is complete in both directions.
The configuration-catalogue test is dynamic and passes. Four TODO markers in total.

---

## 7. Not covered by this audit

Schedule these separately; absence from the checklist does not mean they are sound.

| Area | Not reached |
|---|---|
| Accuracy | `services/trade_svc/deepdive/engine.py` and its siblings (rank board, market filter, model book, trade plan, Markov forecast); the simulator replay path; `uniform_strike_grid`, net premium and the hedge-pressure panel; `rescue.assess_position_risk` scoring; the HIRO model beyond its bad-input handling; `covered_call_candidates` ranking; whether Schwab's daily history includes today's partial bar |
| Security | `services/options_svc/tools_public.py` and `public_chain.py` line by line, including the quote-stripping; the public Gamma page's gating inside `gamma.py`; NiceGUI and socket.io internals; a full secret scan of git history (five patterns only, `docs/` excluded); quote-publishing licence terms |
| Architecture | Anything on the server; the proxy's token-refresh state machine and streaming reconnect; scheduler bodies of the news, market, trade and portfolio services; `paper_adjust` transaction boundaries beyond confirming there is no lock |
| Performance | Every production magnitude (see section 5); the portfolio service's streaming path; news adapters; client-side chart repaint cost |
| Re-audit (market-data store) | Real Schwab behaviour: whether a locally cut chain equals a narrow fetch, when the day-count in the keys flips, whether the daily bar is final ten minutes after the close, what Schwab returns for an all-invalid quote list, how far Schwab's gamma sits from the model on expiry afternoons. Production memory and timings. The full web-app suite was not run against the change |
| Code quality | Function-level dead code; `docs/CHANGELOG.md` and `docs/plans/`; test quality of the engine suites; the static site's JavaScript. No full test suite was run |

---

## 8. Change log

Add one line whenever a row's status changes or a server check is recorded.

| Date | Item | Change | Commit |
|---|---|---|---|
| 2026-10-03 | — | Audit completed; 104 findings opened (3 Critical, 21 High, 40 Medium, 40 Low); 4 carried forward | — |
| 2026-10-03 | — | Re-audit of `c89b1d5..ad873ec` (market-data store). 34 findings added in section 4.8 (1 High, 12 Medium, 21 Low); 11 are live as shipped, 23 bite only after a switch. Section 1.5, clusters J and K, wave S and checks SV-18 to SV-23 added | — |
| 2026-10-03 | CQ-01 | Done. Confirmed at re-audit: name bound, degrade counted, tests call the real function | `ade9c87` |
| 2026-10-03 | PF-01 | Partly done; lowered from High to Low. Four market-data routes return bytes | `e86e9cb` and following |
| 2026-10-03 | CQ-02 | Partly: lint gate green again; no hook installed | `ade9c87` |
| 2026-10-03 | AC-03 | Done. The paper broker fills a single-leg close; two tests drive the real broker | `e0db4d6` |
| 2026-10-03 | AC-01, AC-02 | Done. One settlement rule for the Account, the Ledger and captured signals; a settle-only pass at 15:05 CT; the outcome row records what it settled against | `f0ed8fd` |
| 2026-10-03 | AC-04 | Done, after measuring (SV-13). Condors are sized and reserved off the wider wing | `fe4402e` |
| 2026-10-03 | AC-05 | Done. Unlimited-risk structures return the unlimited sentinel; net-short-put structures are scanned to zero | `7c8d563` |
| 2026-10-03 | AC-40 | Done. No band, an `unknown` bridge regime and no velocity without a composite reading. The numeric zero itself is left open as AC-60 | `7cbdecb` |
| 2026-10-03 | AC-41, AC-42 | Done. Session structure and profile read today's session; the daily frame is weighted 3.0; an unknown timeframe name raises | `8fed641` |
| 2026-10-03 | AC-43 | Done. A P/E or PEG at or below zero carries no valuation reading | `65a563b` |
| 2026-10-03 | AC-44 | Done. No benchmark return unless the history reaches back to the entry | `9a51eed` |
| 2026-10-03 | AC-45 | Done. Relative-strength labels are derived from the period | `f03482c` |
| 2026-10-03 | AC-100 | Done. Shadow records `shadow_moving_*` for today's bar | `ef67cf6` |
| 2026-10-03 | AC-07 | Done, after measuring (SV-25). A later leg is priced at the chain's dividend yield and its own mark-implied volatility | `a553038` |
| 2026-10-03 | AC-19, AC-60 | Opened (both Medium) while fixing AC-07 and AC-40 | — |
| 2026-10-03 | SV-12, SV-13, SV-14, SV-24, SV-25 | Recorded from the production stores and proxy, read-only | — |
| 2026-10-03 | SE-01, SE-02, SE-08 | Done. The proxy's account routes fail closed, the order route is deleted, the passthrough is an allow-list behind the secret, and the secret file is gitignored | `88b6ebc` |
| 2026-10-03 | AR-02 | Done. One lock for every paper-book mutation; a close is one transaction on an OPEN row | `95319de` |
| 2026-10-03 | AR-01 | Done. Promote checks before it stops and rolls back after | `1cd9a2d` |
| 2026-10-03 | CQ-02 | Done. A commit hook, a suite test and a reporting editor hook enforce the lint gate; CI green again | `9e3b700` |
| 2026-10-03 | AR-03 | Done. Failed units, a failed backup and an expiring Schwab sign-in send a push | `007c63b` |
| 2026-10-04 | AR-04 | Done. Backup omissions, weekly retention, prune-after-result and a tested restore | `dbfc3e5` |
| 2026-10-04 | PF-02 | Done, after measuring (SV-10). A priority lane in the proxy's limiter, used by the collection poll | `427ae50`, `dfe84d1` |
| 2026-10-04 | CQ-03 | In progress. Command table with a dispatcher-applied replay guard (`calc_rate` was unguarded); collector tiers in their own module; a line ceiling on `compute.py` | `86e65d1` |
| 2026-10-04 | CQ-04 | In progress. The Desk's row builders are module-level; ceilings on the three large `render()` functions | `3ac2339` |
| 2026-10-04 | SV-01, SV-09, SV-10, SV-16 | Recorded. SV-01 read `200`: production has no proxy secret | — |
