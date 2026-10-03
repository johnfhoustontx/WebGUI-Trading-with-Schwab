# Application Audit — Scorecard and Open-Items Checklist

| | |
|---|---|
| **Audit date** | 2026-10-03 |
| **Commit audited** | `c89b1d5` (tip of `main`) |
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

| Category | Score | July | Critical | High | Medium | Low | Open burden |
|---|---|---|---|---|---|---|---|
| Code Quality | **5.5** | 7.0 | 0 | 4 | 6 | 6 | 34 |
| Performance | **6.8** | 6.5 | 0 | 2 | 6 | 7 | 27 |
| Architecture | **5.5** | 7.0 / 5.0 | 0 | 4 | 7 | 6 | 36 |
| Security | **7.5** | 7.0 | 0 | 2 | 5 | 11 | 29 |
| Accuracy | **6.4** | 7.7 | 3 | 9 | 16 | 10 | 102 |
| **Overall** | **6.3** | 6.5 | **3** | **21** | **40** | **40** | **228** |

- **Open burden** is the trackable number: Critical 8, High 4, Medium 2, Low 1, summed
  over open rows. Subtract a row's weight when its status becomes Done, Accepted or
  Refuted. Category scores are re-judged only at the next audit.
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
| | Static analysis and enforcement | 4.0 | Configured lint gate is red on `main`; no git hook installed; type check covers about 1% of the code |
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
| 1 | SE-01 | The proxy's `/passthrough` route reaches the real brokerage account API without the shared secret | Highest-value asset; one-hour fix |
| 2 | SE-02 | The proxy secret check fails open, and an unused endpoint can place real orders | The application is paper-only; nothing needs this endpoint |
| 3 | AC-01 | Captured signals are closed as "expired" on expiry morning | Corrupts the outcome data every calibration and measured study rests on |
| 4 | AC-02 | The paper Account and Ledger settle expiries the next trading day at that morning's price | Booked profit and loss is wrong for every trade held to expiry |
| 5 | AC-03 | Income positions can never be closed by a rule | The 50% target and 21-day exit described as live never execute |
| 6 | AR-01 | Promote takes production down before any step that can fail, with no rollback | Every other fix in this document ships through it, with no staging |
| 7 | CQ-01 / CQ-02 | A live undefined-name bug has sat on `main` for two weeks because the lint gate is not enforced | The mechanical backstop is off |
| 8 | AC-40 | A data outage publishes "Strong Bear", pushes it to the phone and blocks put credit spreads | A failure that looks like a confident reading |
| 9 | AR-02 | Paper-book changes are not serialized between the scheduler and the command consumer | A double close overwrites realized profit and loss |
| 10 | AR-03 / AR-04 | Failures are only visible in an open browser tab; restore is undocumented and the login store is not backed up | Continuity |

### 1.4 Closed as fixed, found not fixed

This application has a history of audit items being closed while the defect remained.
Four more instances surfaced:

| Item | What the record says | What the code does |
|---|---|---|
| CQ-01 | Docstring: `evaluate_regime` is "eagerly imported at module top" | The import was removed on 2026-09-19; the name is undefined and the error is swallowed |
| CQ-02 | `pyproject.toml`: the rule set "passes CLEAN on the current tree" | `ruff check .` fails |
| AC-03 | Income positions were given marks and exit rules on 2026-09-11 | The repricer was repaired; the broker that fills the close was not |
| AC-40 | The absent-composite fix publishes no band when there is no reading | The main producer returns `"0.00"`, which is treated as a real reading |

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

### 2.3 Fixes that move live numbers

These accuracy fixes change what the application reports or decides on the day they
land. Each needs a before-and-after check, and several invalidate earlier measurements.

| Fix | What moves | Check after landing |
|---|---|---|
| AC-01, AC-02 | Every outcome and booked result for trades held to expiry | Count how many historical outcomes closed before 15:00 CT on their expiry date. The score calibration, the entry-volatility-rank study and the profit-lock ladder replay were all computed on these outcomes and should be re-run |
| AC-03 | Income positions begin closing at target and at 21 days | Watch the first manage cycle; expect several closes at once |
| AC-04 | Iron-condor quantity and reserved risk fall | Compare the day's condor signals before and after |
| CQ-01 | Rescue heat and candidate ranking gain the regime modifier again | Compare the at-risk board before and after |
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
| AC-01 | Critical | R+M | Captured signals close as EXPIRED on expiry morning. The gate is `dte <= 0` with no 15:00 CT check. They close at the live mark, or at intrinsic against the **entry** price when unmarkable. Two tests pin the behaviour. Feeds `signal_outcomes` and the nightly calibration | `services/options_svc/compute.py:3534-3551`; `tests/test_compute.py:1819,1830` | Gate on `paper_engine.should_settle`; settle at intrinsic against the session close; defer when no close is available; rewrite both tests against an independent expectation | M | + | · | + | · | ■ | Open |
| AC-02 | Critical | R+M | Paper Account and Ledger settle expiries the next trading day at that morning's live price. The cycle runs 09:00–14:00 CT; settlement needs 15:00 or later. Reproduced: true +$98.70 at Friday's close booked as −$401.30 on Monday | `services/options_svc/scheduler.py:207`; `options-scanner/paper_engine.py:633-649,752-753`; `compute.py:2966` | Add a settle slot at about 15:05 CT; for a past expiry settle against that date's daily close, not a live quote | M | + | · | + | · | ■ | Open |
| AC-03 | Critical | R+M | Income positions (short put, covered call) can never be rule-closed. `simulate_fill_price` raises for anything but PCS, CCS, IC, so each exit is rejected every hour and a rejected order row is written | `options-scanner/paper_broker.py:49-72`; `paper_engine.py:844-856` | Add a single-leg branch using `fill_model.realistic_single_fill` | S | + | + | · | · | ■ | Open |
| AC-04 | High | C+M; frequency I | Iron condors with unequal wings are sized and reserved off the put width while max loss uses the wider wing. Reproduced for 5-wide and 10-wide: booked $560, true $1,560 | `options-scanner/scanner_engine.py:1401,1424`; `paper_engine.py:265,294` | **Measure first**: count condors in `signals.db` whose wing widths differ. Then emit `width = max(...)` or size from `entry_max_loss` | S | + | · | · | · | ■ | Open |
| AC-05 | High | R | Calculator MAX RISK shows a finite figure for unlimited-risk structures (short straddle $4,310). The scan covers only 0.5× to 1.5× spot. Also on the public calculator | `options-scanner/options_calculator.py:720-735`; `webgui/pages/options/calculator.py:726-734` | Use the tail-slope rule `payoff_metrics` already has; scan to zero when net short puts | S | + | · | · | · | ■ | Open |
| AC-06 | Medium | R | The two time-to-expiry helpers differ by one hour across a daylight-saving change. Calculator expiry column showed −$287.19 against a true −$300.00 | `compute.py:8173-8183`; `options_calculator.py:74-79,1085` | Subtract in UTC | S | + | · | + | · | ■ | Open |
| AC-07 | High | R; Schwab dividend handling I | Zero-dividend pricing reaches long-dated calendars and diagonals. On a 1.2% yielder, a 30/365-day call calendar's max profit is overstated 47% and a put calendar understated 28% | `options-scanner/strategy_scanner.py:171` | Carry a per-symbol yield, or a forward implied from put-call parity | M–L | ! | · | ! | · | ■ | Open |
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

### 4.2 Accuracy — indicators, scoring, regime, factor model, portfolio

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-40 | High | R+M | With every fetch failing, the composite is the string `"0.00"`, which is treated as a real zero: "Strong Bear / Short / 0.70x" is published and pushed. The bridge writes `strong_bearish`, and the scanner then blocks put credit spreads | `sentiment-dashboard/live_composite.py:88-98,334-340`; `services/sentiment_svc/compute.py:1584-1590` | Treat aggregate confidence 0 or total ≤ 0 as absent at both sites; write `unknown` to the bridge; add a test driven from a dead client | S | + | · | + | · | ■ | Open |
| AC-41 | High | R | The Day gauge's session structure and profile are computed on a 10-day frame. A −3% day after nine up days scored +1.00 | `services/sentiment_svc/compute.py:513-533,697-712` | Slice with `_today_session` first; confidence 0 with fewer than six bars | S | + | + | · | · | ■ | Open |
| AC-42 | High | R+M | The daily frame is weighted 1.0, not 3.0, in EMA alignment: the frame is keyed `"1day"`, the weight table `'daily'` | `compute.py:482,613,629,1308`; `shared/analysis_lib/config.py:35`; `technical.py:407` | Rename the key, or raise on an unknown key | S | + | · | + | · | ■ | Open |
| AC-43 | High | R+M | Long Term verdict: a price-to-earnings ratio at or below zero scores +60 and a PEG at or below zero scores +40, so a loss-maker gets the maximum valuation score | `trade-analyzer/src/analysis/scoring.py:91-111` | Admit both into valuation only when above zero | S | + | · | · | · | ■ | Open |
| AC-44 | High | R | Portfolio "vs sector" and "vs SPY" compare different horizons once a position is older than 12 months (+0.889 shown, 0.000 true) | `portfolio-analyzer/src/evaluation.py:23-44,184,295` | Return None when history starts after the entry date, or fetch back to entry | S / M | + | ! | · | · | ■ | Open |
| AC-45 | High | R+M | Portfolio relative-strength labels are one horizon too short: the 5, 21 and 63-day figures are labelled 1D, 1W, 1M | `shared/analysis_lib/sector_analysis.py:290-300` | Map the label from the period | S | + | · | · | · | ■ | Open |
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

### 4.3 Security

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SE-01 | High | R+M | The proxy's `/passthrough` route takes `endpoint` from the query string unvalidated and without the shared secret. `/../../trader/v1/...` normalises onto the brokerage account API, reaching accounts, positions, orders and transactions | `schwab-proxy/schwab_proxy.py:707-719,352` | Allow-list the endpoint, or replace the route with the two named routes its callers use; require the secret | S | + | · | + | ■ | · | Open |
| SE-02 | High if unset | C+M; production state I | `require_secret` returns without checking when no secret is configured. `POST /orders/{account_hash}` forwards a real order and nothing calls it | `schwab_proxy.py:438-439,906-916` | Delete the order route. Make the account routes fail closed with no secret | S | + | · | + | ■ | · | Open |
| SE-03 | Medium | C | `POST /login` parses an unbounded body before any throttle; the edge sets no body limit; the private web app has no memory cap | `webgui/main.py:324-327`; `deploy/caddy/generate_caddyfile.py:380-395` | Body limit at the edge; reject on length before parsing; a memory cap on the unit | S | · | + | · | ■ | + | Open |
| SE-04 | Medium | R | The public process's read-only Redis layer can be absent while the startup check passes: a URL with no user resolves to the default user with the admin password from the environment | `webgui/live_main.py:155-156`; `shared/bus/client.py:138-140` | Remove the admin password from `.env.live`; require a non-default user; at startup attempt a write and refuse to serve unless it is denied | S | + | · | + | ■ | · | Open |
| SE-05 | Medium | R | The Telegram bot token and Discord webhook address are written to logs on connection errors | `shared/notify/channels.py:315,342,368,377,406` | Log the exception type only, as `x_post.py` already does. Rotate if section 5 finds hits | S | + | · | · | ■ | · | Open |
| SE-06 | Medium | R | One address posting every 17 seconds locks the owner out of sign-in indefinitely: every refusal counts toward the global lock | `webgui/login_page.py:300`; `webgui/auth.py:396-398` | Count only attempts that reached the password hash; exempt a valid remember-device cookie | S–M | + | · | · | ■ | · | Open |
| SE-07 | Low | C; mode I | The Schwab token file is written non-atomically with the default file mode | `schwab_proxy.py:225-228` | Temp file, mode 0600, atomic replace. Also closes the token part of AR-09 | S | + | · | + | ■ | · | Open |
| SE-08 | Low | R+M | `shared/proxy_secret.txt` is documented as gitignored and is not | `.gitignore` | Add the pattern | S | · | · | · | ■ | · | Open |
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
| AR-01 | High | C+M | Promote stops production before the steps that can fail (fetch, pull, install). No trap, no recorded previous commit, no rollback. The post-start check probes only the proxy and the web app | `tools/promote.sh:37,56-66,107-108` | Fetch and verify fast-forward before the stop; trap that restarts on failure; record the previous commit; probe all six service health endpoints | M | · | + | ■ | · | + | Open |
| AR-02 | High | C+M; race I | Paper-book mutations are not serialized between scheduler and command threads. `_close` is three separate commits. `close_position` has no `status='OPEN'` condition, so a second close overwrites the first | `options-scanner/paper_account_db.py:543-552`; `paper_engine.py:403-414`; `services/options_svc/compute.py:2905-2917` | One re-entrant lock on every book-mutating entry point; add the status condition with a row-count check; one transaction for `_close` | M | + | · | ■ | · | + | Open |
| AR-03 | High | C | Failure detection exists only in an open browser tab. No unit has an on-failure action. Nothing warns before the 7-day Schwab token lapses. A failed backup is silent | `webgui/main.py:2168-2170`; `deploy/systemd/generate_units.py`; `webgui/pages/status.py:263-275` | An on-failure notifier unit on the existing channels; a "token expires within 48 hours" push; a push when the backup fails | M | · | · | ■ | ! | + | Open |
| AR-04 | High | C+M | Restore is undocumented and untested. The backup omits the login store, the calendar service-account key, two vendor keys and the site's generated state. Three generations, weekdays only; pruning runs before the failure check | `tools/backup_local.py:69,79,82-103,116-129,354` | Add the files; write a restore section and run it once into a scratch directory; keep a weekly generation; prune only after a clean run | M | · | · | ■ | ! | · | Open |
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
| PF-01 | High | R (synthetic) +M; production size I | The proxy parses every Schwab payload and returns a dictionary, so the framework encodes it again. About 0.6 s of processor time for one index-sized chain, during which `/health` and every other call stall | `schwab-proxy/schwab_proxy.py:366,625-683` | Return the upstream bytes on the market-data routes | S | + | ■ | · | · | + | Open |
| PF-02 | High | I (arithmetic) | The 1-minute collection poll has no headroom on the shared 5 requests per second at quarter-hours; the limiter has no priority. Twice the watchlist cannot fit | `schwab_proxy.py:125,320-337`; `options-scanner/gex_collector.py:67`; `services/options_svc/scheduler.py:501-504` | **Measure first** (count the skip warnings). Then a priority lane in the limiter, and a slower cadence for the long tail | M | · | ■ | ! | · | ± | Open |
| PF-03 | Medium | C+M; magnitude I | Six blocking stream pollers, every scheduler branch and every command handler share one default thread pool. With eight long jobs no stream is read | `services/_scaffold.py:254-264,311`; `services/options_svc/app.py:38-54` | A dedicated thread per consumer loop; a separate bounded pool for the rest | S | · | ■ | + | + | · | Open |
| PF-04 | Medium | C | `cmd:options` is one serial queue. A manual rescan or a Finder scan holds up reprice, paper and rescue commands; a `paper_create` queued more than 180 seconds is refused as stale | `services/options_svc/handlers.py:59,3233-3533` | Fast lane for math and local-database commands, slow lane for anything that fetches. **Requires AR-02 first** | M | ! | ■ | ! | · | + | Open |
| PF-05 | Medium | C; size I | The Market Scanner sends the whole day's union (about 4.5 MB by the close) to the browser on every repaint and every 5-minute re-stamp | `webgui/pages/options/scanner.py:656-712,1062-1081,1291-1307` | Server-side paging, or send only changed columns | M | ! | ■ | · | · | · | Open |
| PF-06 | Medium | C; volume I | Append-only history keys are rewritten in full, and read back in full for comparison, every minute | `services/options_svc/handlers.py:1714-1722`; `shared/bus/client.py:174-199` | Small: skip the comparison during collection. Large: an append structure | S / L | ! | ■ | ! | · | ! | Open |
| PF-07 | Medium | R (synthetic) | Each browser tab parses and holds its own copy of large payloads (1.4 MB of JSON becomes 4.3 MB in memory). Fifty public visitors approach the memory limit | `webgui/pages/options/gamma.py:3034,3063`; `webgui/bus_client.py:86-93` | A process-wide `(view, version)` cache so tabs share one parse | S–M | ! | ■ | · | + | · | Open |
| PF-08 | Medium | R (synthetic) +M | `signal_marks` grows without bound and its latest-mark query has no composite index: 280 ms on 640,000 rows against 0.1 ms with one | `options-scanner/signal_db.py:45-62,336-362` | Add an index on `(signal_id, mark_ts)`; batch a cycle's marks in one transaction | S | · | ■ | · | · | · | Open |
| PF-09 | Low | R (synthetic) | Every 15-minute scan rewrites a year of realized-volatility rows per symbol (2.65 s, about 18,500 unchanged rows) | `options-scanner/scanner_engine.py:1754-1760`; `shared/iv_history.py:424-479` | Once per session per symbol; drop two redundant indexes | S | + | ■ | · | · | · | Open |
| PF-10 | Low | I | The autoscan re-fetches a year of daily history and a volatility chain per symbol every slot: half of each scan's burst | `scanner_engine.py:168-174,1954-1958` | A time-to-live on history; refresh the volatility chain every second or third slot. Measure score drift first | S–M | · | ■ | · | · | ! | Open |
| PF-11 | Low | C | The Redis client has no socket timeout; version probes run on the event loop, so a Redis stall freezes every tab silently | `shared/bus/client.py:133-135` | Timeouts on the web tier's client only, not on the blocking stream read | S | · | ■ | · | + | · | Open |
| PF-12 | Low | C | Calculator, Simulator, Rescue and Sentiment still use separate uncoalesced one-second version probes and some on-loop reads | `calculator.py:1821-1823`; `simulator.py:1115-1119`; `rescue.py:1318-1321`; `sentiment.py:1067-1075` | One batched probe per page; reads off the loop | S per page | + | ■ | · | · | · | Open |
| PF-13 | Low | I | A restart fires rescan, collection and manage together and demands roughly twice the per-minute budget. No stop timeout is set on the units | `services/options_svc/scheduler.py:57-63,553-666` | Persist last-run slots (with AR-15); stagger; set an explicit stop timeout | S–M | · | ■ | · | · | + | Open |
| PF-14 | Low | I | The daily history purge runs inside the first collection tick of the day | `services/options_svc/compute.py:4941`; `options-scanner/gex_history_db.py:957-978` | Move it to the post-close window | S | · | ■ | · | · | · | Open |
| PF-15 | Low | R (synthetic) | The chain grid renders every strike of an expiry as one block (675 KB for 500 strikes) | `webgui/pages/options/entry_panel.py:311`; `chain_grid.py:309-342` | A window around spot with a "show all" control | S | · | ■ | · | · | · | Open |

### 4.6 Code quality

| ID | Sev | Conf | Finding | Where | Fix | Eff | CQ | PF | AR | SE | AC | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CQ-01 | High | R+M | `_rescue_regime()` calls `evaluate_regime`, which is not defined at module level. The error is swallowed, so the Rescue regime modifier has been dead since the import was removed. All seven tests replace the function with a stub | `services/options_svc/compute.py:9523,9528` | Import inside the function; one test that calls the real function; report the degrade | S | ■ | · | · | · | + | Open |
| CQ-02 | High | R+M; CI state I | The lint gate exists in three places and is enforced in none: `ruff check .` fails on `main`; no git hook is installed; the editor hook discards unfixable findings and does nothing in a worktree | `pyproject.toml`; `.pre-commit-config.yaml`; `.claude/hooks/ruff_fix.py:36-53` | Install the pre-commit hook; make the editor hook print unfixable findings; look at the Actions tab | S | ■ | · | · | + | + | Open |
| CQ-03 | High | R | `options_svc/compute.py` is 10,548 lines and growing about 1,000 a month: 278 functions, 199 function-local imports, 193 broad exception handlers. `handle_command` is a 370-line chain. Tests patch the module by name at 495 sites | `services/options_svc/compute.py`; `handlers.py:3164` | Split by domain behind stable public functions; a dispatch table. Do after clusters B, C, D | L | ■ | · | + | + | ! | Open |
| CQ-04 | High | R | Page `render()` functions have doubled or tripled: `gamma` 1,484 lines with 62 nested functions, `desk` 1,057, `calculator` 1,027 | `webgui/pages/options/gamma.py:2169`; `desk.py:3183`; `calculator.py:968` | Extract a builder per panel, starting with gamma and calculator | L | ■ | · | + | ! | · | Open |
| CQ-05 | Medium | C | Continuous integration omits `services/news_svc`, `shared/notify/tests`, `tests/`, `deploy/` and the hook tests; the options-scanner suite is non-blocking and still deselects tests that now pass; no type-check job. Promote consults no check | `.github/workflows/ci.yml:136-207` | Add the rows; drop the soft flag and deselects; add the type check | S | ■ | · | + | + | + | Open |
| CQ-06 | Medium | R | The silent-degrade guard matches the word "log." in source text, so two handlers around paid Claude calls pass because their error page mentions "the service log." The guard covers `services/` only | `services/tests/test_no_silent_degrades.py:33,56-58`; `compute.py:7150,7641` | Match call nodes, not substrings; report the degrade in both handlers; extend to `options-scanner/` and `shared/` | S | ■ | · | · | + | + | Open |
| CQ-07 | Medium | R | 31 `_num` helpers with 16 different behaviours (nine pass NaN through); 20 `_finite` helpers with five | Across `services/`, `webgui/pages/`, `shared/` | One `shared/numeric.py` with explicitly named variants, migrated one behaviour group at a time. Never by search-and-replace | M | ■ | · | · | + | ± | Open |
| CQ-08 | Medium | R | Lint and type coverage exclude the four engine folders as "copied verbatim", though they are the most actively changed and already pass the configured rules. A quarter of functions carry any annotation | `pyproject.toml`; `pyrightconfig.json` | Delete the four excludes; then adopt the unused-import and redefinition rules | S | ■ | · | · | · | + | Open |
| CQ-09 | Medium | R | `CLAUDE.md` is about 300 KB against its own ~100 KB target, with stale present-tense claims and internal contradictions (test counts, the `_clamp` duplication, the web tier's "zero sqlite3") | `CLAUDE.md` | Repeat the 2026-08-16 relocation; add a size test | M | ■ | · | · | · | + | Open |
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

---

## 5. Facts to confirm on the server

These convert inferred findings into measured ones. All are read-only. Run from the
production checkout. Record the result and date in the last column.

| ID | Settles | Command | Expect | Result |
|---|---|---|---|---|
| SV-01 | SE-02 | `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/accounts` | `401` | |
| SV-02 | SE-01, SE-12 | `tailscale serve status ; tailscale funnel status ; tailscale status` | Funnel off; every listed device can reach the proxy | |
| SV-03 | SE-07 | `stat -c '%a %n' schwab-proxy/proxy_tokens.json shared/tokens.json shared/appsettings.json shared/webgui_auth.json shared/notifications.json shared/google_calendar_sa.json .env .env.live` | `600` on all | |
| SV-04 | SE-04 | `grep -c '^MEMURAI_PASSWORD=' .env.live` and inspect the user in `REDIS_LIVE_URL` (do not print the password) | `0`, and a non-default user | |
| SV-05 | SE-10, AR-12 | `redis-cli ACL GETUSER live` ; `redis-cli CONFIG GET save` ; `redis-cli CONFIG GET appendonly` | The grant as documented; a stated persistence mode | |
| SV-06 | Network | `ss -ltnp \| grep -E ':(6379\|8100\|8500\|8501\|82[0-9]{2})\b'` | Every listener on `127.0.0.1` | |
| SV-07 | SE-05 | `grep -rlE '/bot[0-9]+:\|api/webhooks/[0-9]+/' logs/ \| head` | No files. Any hit means rotate | |
| SV-08 | SE-03 | `systemctl --user show trading-prod-webgui -p MemoryMax` | Currently unlimited | |
| SV-09 | PF-03 | `nproc` | Sets the thread-pool size (`min(32, cores + 4)`) | |
| SV-10 | PF-02 | `journalctl --user -u trading-prod-options_svc --since today \| grep -c "still running; skipping"` | The count of lost collection slots per day | |
| SV-11 | PF-08 | `sqlite3 options-scanner/data/signals.db "select count(*) from signal_marks"` | Row count | |
| SV-12 | AC-01 | Count rows in `signal_outcomes` with reason `EXPIRED` whose close time is before 15:00 CT on the expiration date | The number of contaminated outcomes | |
| SV-13 | AC-04 | Count condor rows in `signals` where the call-wing width differs from the put-wing width | Zero would make AC-04 latent | |
| SV-14 | AC-03 | Count open rows in `paper_positions` with strategy `SHORT_PUT`, `NAKED_PUT` or `COVERED_CALL`, and rejected orders against them | The positions that cannot close | |
| SV-15 | SE-14 | `.venv/bin/python -m pip_audit` | The installed environment, not only the lock | |
| SV-16 | CQ-02, CQ-05 | The repository's Actions tab on GitHub | Whether the lint job is red or not running | |
| SV-17 | PF-01 | `py-spy top --pid $(systemctl --user show -p MainPID --value trading-prod-proxy)` during market hours | `jsonable_encoder` near the top | |

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
| Code quality | Function-level dead code; `docs/CHANGELOG.md` and `docs/plans/`; test quality of the engine suites; the static site's JavaScript. No full test suite was run |

---

## 8. Change log

Add one line whenever a row's status changes or a server check is recorded.

| Date | Item | Change | Commit |
|---|---|---|---|
| 2026-10-03 | — | Audit completed; 104 findings opened (3 Critical, 21 High, 40 Medium, 40 Low); 4 carried forward | — |
