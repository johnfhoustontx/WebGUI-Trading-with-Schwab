# Trade Analyzer long/short — implementation plan

Companion to [`2026-08-22-trade-analyzer-longshort-design.md`](2026-08-22-trade-analyzer-longshort-design.md).
TDD throughout: write the failing test, watch it fail for the right reason, then
the minimal code. Every phase ends with the suite green, verified running in
dev, and docs + `page_help.py` updated in the same commit.

## ⛔ Promotion is deferred until Phase 4 completes (decision, 2026-08-22)

**No `promote.bat` run until Phase 4 is done.** `main` stays where it is; work
accumulates on `Using_Highcharts` and is verified in dev only. Three consequences
this plan must absorb rather than discover later:

1. **Dev runs with `schedulers: False`** (verified: `ENV_FLAGS`), so **any
   scheduler-driven job written in a service will not run in dev** — it would sit
   inert until promotion. That defeats the entire reason the accrual stores are
   sequenced first (they pay in calendar time, not effort). **Therefore 1.4's
   sweep is a standalone script driven by a Windows scheduled task, not a service
   scheduler job** — it accrues under suppression, and the design is better
   anyway (the fit script has the same never-import-from-a-service property).
2. **Prod keeps the Phase-0 wall-side bug** for the duration. It only misfires
   when a chain has strikes on one side of spot only, but Rescue is decision
   support for real positions — a deliberate, accepted cost, not an oversight.
3. **The promotion when it comes is large** (P1–P4 in one move), so the
   `requirements.lock` dry-run against prod's venv (see 1.2) becomes *more*
   important, not less, and deserves a rehearsal before the real run.

⚠ "Phase 4 complete" means **the phase ran to a decision**, not that its
acceptance gate passed. A failed gate — the Phase-0 artifact staying primary — is
a completed Phase 4 with a documented negative result, and does not by itself
extend the freeze.

Test commands (worktrees have no venv — absolute path):

```powershell
"D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest services\trade_svc -q
"D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe" -m pytest services\options_svc -q
cd trade-analyzer ; ..\.venv\Scripts\python -m pytest tests -q
cd webgui ; ..\.venv\Scripts\python -m pytest . -q
```

Baselines **measured 2026-08-22** (not copied from another doc): trade_svc **79**,
options_svc **1222** (1218 + the 4 Phase-0 wall tests), trade-analyzer **251**,
webgui **2546**. Compare the failing *set*, not the count. ⚠ The webgui figure
supersedes the 2320 recorded on 2026-08-20 — work landed in between; a count
copied forward is exactly the trap this repo documents.

---

## Phase 0 — bench-clearing ✅ SHIPPED 2026-08-22

- [x] **Archive + refit.** Old artifact/report copied to
      `trade-analyzer/data/archive/2026-06-28/`; `fit_swing_model.py` re-run
      against the live proxy → artifact `2026-08-22`. **OOS IC +0.0367 →
      +0.0206**; see the design doc's Phase 0 findings — this result
      re-prioritises Phase 4 and must not be glossed.
- [x] **Wall-side bug** (`options_svc/compute._gex_from_snapshot`): 4 new tests
      in `services/options_svc/tests/test_compute.py`, two of which failed
      first with exactly the reported symptom (`assert None == 452.0`). Sides
      now split on spot; an unsidable lone wall is dropped.
- [x] **Stale docstring** in `webgui/pages/trade.py` — replaced with what is
      actually true, including which Investor inputs are structurally absent.
- [x] **This design/plan pair.**

---

## Phase 1 — data foundations

### 1.1 Short interest into `Fundamentals`
- **Test** (`trade-analyzer/tests/analysis/test_fundamentals.py`): a Schwab
  payload carrying `shortIntToFloat` / `shortIntDayToCover` parses into the new
  fields; a payload lacking them leaves `None`; the fields do **not** count
  toward `is_sufficient()` (it stays the four core fields).
- **Code**: two fields on the dataclass + two reads in
  `parse_schwab_fundamentals`. No new I/O — the payload is already fetched.

### 1.2 ⛔ SUPERSEDED — earnings dates and short interest need a real source

**Decision, 2026-08-22: no finviz.** Task 1.1 found that Schwab serves
`shortIntToFloat` / `shortIntDayToCover` as a **0.0 sentinel for every symbol**
(verified live: AAPL, TSLA, GME, CVNA all 0.0 while `peRatio` and
`returnOnEquity` in the same payload were correct). That promoted finviz from a
convenience for earnings dates into the **sole supplier of both the earnings
gate and the entire short side** — i.e. load-bearing scraping infrastructure,
with the terms-of-service and fragility exposure that implies. The user declined
it and asked for a different source first.

So this task is now: **evaluate real sources, then wire the winner.** Candidates
under review include FINRA's official short-interest files, exchange
publications, licensed vendor APIs, and the broker APIs the user named
(Public.com, TradeStation, moomoo/Futu). Requirements: official or licensed
rather than scraped; usable for **data alone without funding a new account**;
daily-batch cadence is sufficient; a 1–2 week lag is acceptable for short
interest because it gates a coarse threshold, while an earnings DATE must be
accurate.

#### ✅ Short interest — SOLVED 2026-08-22, free and official

**FINRA's Query API ÷ Schwab's `marketCapFloat`**, in
`services/trade_svc/short_interest.py`. Verified live end-to-end: the whole US
universe (22,341 rows) in ~7 s, unauthenticated, and FINRA's Specific Terms for
Equity Data permit "non-commercial personal or professional use" plus derivative
data. Days-to-cover arrives **pre-computed**. The float denominator was already
in a payload every analyze fetches — Schwab's `marketCapFloat` is float in
**shares**, despite the name.

Four things the live verification forced, each of which would otherwise have
shipped as a silent bug:
- **The API returns CSV, not JSON** (`content-type: text/plain`, every field
  quoted) — its docs imply otherwise. It does honour `Accept: application/json`.
- **Filter on `settlementDate`, never `limit` + client-side sort.** The default
  ordering is not newest-first; a naive `limit: 200` returned a cycle **four
  months stale** while looking perfectly healthy.
- **Cap the ratio at 100%.** FINRA is not split-adjusted and its
  `stockSplitFlag` only appears in the cycle AFTER a split, so a reverse split
  between settlement and today puts a pre-split numerator over a post-split
  float. Measured live: **BYND computes to 783%**. `percent_of_float` returns
  `None` there — reporting it would fire the gate hardest exactly when the data
  is meaningless, the same shape as a missing reading clamping to an extreme.
- **The gate fires on EITHER leg**, not both. Float is the contested term
  (CHWY: 89% / 51% / 12% depending on whose float), while days-to-cover is
  FINRA's own computation and never touches float. Measured live, **GME fires on
  days-to-cover (17.06) while its 13.14% of float sits under the threshold** —
  exactly the case an AND would have missed.

⚠ **A float artifact can still fire it** — CHWY reads 89.46% on Schwab's float
against ~12% on shares outstanding. For a gate that *blocks* shorting that is
the safe direction (a false positive costs a trade you skip; a false negative
costs a squeeze), but it is a known imprecision, not a measurement.

Cadence is bi-monthly, published 9–12 days after settlement, so a reading is
**8–27 days old**. Fine for a coarse gate, and the best any official source can
do; anything advertising daily short interest is a stock-loan model. FINRA has a
weekly-reporting rule change pending — if it lands, only the refresh cadence
changes.

#### ⛔ Earnings dates — still open, and it is a spending decision

No official source exists: SEC 8-K Item 2.02 is retrospective, and no exchange
publishes a forward calendar, so **every forward earnings date is a vendor
product**. Options: Alpha Vantage's bulk `EARNINGS_CALENDAR` free (one call a
night fits the 25/day limit, but a measured 3-month probe was missing mega-caps
— retest `horizon=12month` with a real key before trusting it); FMP ~$49/mo,
whose `includeReportTimes=true` carries a **confirmed-vs-estimated flag**; or
Benzinga via Massive $99/mo for a server-side `date_status`. The flag is the
thing worth paying for — a gate that blocks on a *guessed* date blocks the wrong
weeks and lets the real ones through, silently.

**Until this lands the earnings gate stays dead on both verdicts.** The mirrored
and squeeze gates do not depend on it.

### 1.2b finviz supplement — NOT TAKEN (kept for the record)
- **Test** (`services/trade_svc/tests/test_compute.py`): with a stubbed finviz
  returning an earnings date and Schwab returning `None` for it, the merged
  `Fundamentals` carries the date **and keeps every Schwab value** (fill-only,
  never overwrite); a raising finviz degrades to the Schwab result unchanged;
  the per-symbol/day cache calls finviz once for two analyses.
- **Test** (`services/trade_svc/tests/test_compute.py`): with a date inside the
  window, `position_verdict["gates_triggered"]` contains the earnings gate and
  the verdict is capped at HOLD — the gate that has never fired.
- **Code**: `_fetch_fundamentals` merges; a small module-level day cache.
- ⚠ **`finvizfinance` goes in `requirements.txt` AND `requirements.lock`.** Prod
  has its own venv and `promote.bat` reinstalls only when the lock moves. Verify
  before promoting:
  `"D:\WebGUI Trading Prod\.venv\Scripts\python.exe" -m pip install --dry-run -r requirements.lock`
  must name exactly `finvizfinance` and nothing else.

### 1.3 Un-dead the valuation component
- **Test** (`trade-analyzer/tests/analysis/test_recommendation_investor.py`):
  with `sector_pe_median=None`, `valuation` equals the PEG score **alone** (not
  PEG averaged with a structural zero); with a median present, it is the mean of
  both. A symbol with a strong PEG and no median must score materially higher
  than today's half-weighted result.
- **Test** (`services/trade_svc/tests/test_compute.py`): `analyze` passes a
  computed median, memoized per sector per day.
- **Code**: average only non-`None` sub-scores in `InvestorVerdict`; a
  `_sector_pe_median(sector)` helper over `_SYMBOL_SECTOR` peers.

### 1.4 Start the accrual stores ✅ DONE 2026-08-22
- `services/trade_svc/rec_journal.py` — what the model SAID, one row per
  (symbol, day), upserted so a symbol analyzed five times in an afternoon casts
  one vote rather than five. Label columns (`fwd_5d/10d/20d`, `labeled_at`)
  exist from the first row so Phase 6's labeler is an UPDATE, not a migration.
- `services/trade_svc/fundamentals_history.py` — the point-in-time INPUTS (not
  the score: a score is recomputable from inputs under new weights, inputs are
  not recoverable from a score), plus `sector_pe_median`, since the valuation
  component is relative and the peer median moves too. `margin_expanding` stays
  three-valued — None is "the pair needed to decide was absent", never 0.
- Paths added to `repo_paths` (`TRADE_SVC_DATA`, `REC_JOURNAL_DB`,
  `FUNDAMENTALS_HISTORY_DB`); `IV_HISTORY_DB` re-derived from the same dir.
- **The pytest isolation guard is real and verified**: with no explicit
  `db_path` both writers no-op under `PYTEST_CURRENT_TEST`, and a full 105-test
  run creates no `services/trade_svc/data/` at all. Tests that want the mapping
  pass a `tmp_path`, which bypasses the guard.
- ⚠ **Still open**: the nightly UNIVERSE sweep. Today both stores accrue only
  for symbols actually analyzed. Per the promotion freeze the sweep must be a
  standalone script on a scheduled task, not a service scheduler job — dev runs
  `schedulers: False`, so a service job would sit inert until promotion.

**Phase 1 exit, revised:** Investor valuation ≠ 0 for a symbol with a known P/E
✅ (MSFT scores 35 where the old halving gave 20); both stores accruing ✅.
The earnings gate stays dead until 1.2's source question is settled — it is the
one Phase 1 goal that did not survive contact with the data.

---

## Phase 2 — both sides + context

### 2.1 Mirrored + short gates ✅ DONE 2026-08-22
Three short-only gates on `PositionVerdict`: above a **rising** 200-EMA, sector
in confirmed uptrend, and squeeze risk. `SectorStrength` gained
`in_confirmed_uptrend` (`above_50ema and rs_pct > 0.80`, the mirror of the
existing downtrend rule), defaulted so no existing construction site changes.

Three decisions worth keeping:
- **The 200-EMA mirror requires a RISING average, not just price above it.**
  Price bouncing back above a still-falling 200-EMA is a rally in a downtrend —
  the textbook short entry — so a bare "above the 200" test would gate away
  exactly the setup the short side wants. `_is_rising` compares the EMA to
  itself 20 bars back and returns False when the series is too short, so an
  unknowable slope never gates a trade.
- **Short gates live in their OWN list (`short_gates`), not `gates_triggered`.**
  This surfaced as a test failure and it was the test being right:
  `gates_triggered` answers "why isn't this a BUY?", so a short-only constraint
  there prints "cannot be SELL" on every strong BUY — noise, not a reason.
  Separating them is also what lets the page render each side with its own
  reasons, which is 2.2's whole point. `gates_triggered` is untouched, so the
  change is additive.
- **The squeeze reason is computed UPSTREAM and passed in.** The pure engine
  cannot reach FINRA, so `PositionInputs.squeeze_reason` carries the verdict and
  the threshold policy stays in one place (`short_interest.squeeze_flag`).

Live proof it discriminates: **GME** gates on `Squeeze risk (17.1 days to
cover)`, **AAPL** on `Above a rising 200-EMA`, **CVNA** on neither — 10.09% of
float and 6.02 days to cover clear both legs, so it is shortable per these
gates.

### 2.2 Direction clearance ✅ DONE 2026-08-22
`services/trade_svc/market_filter.py` — pure; `analyze` supplies the SPY series
it already fetches plus a staleness-guarded read of `cache:sentiment:regime`.
Three states per side (`cleared` / `relative_only` / `blocked`), **both sides
always present with non-empty reasons**, because a blocked side WITH its reasons
is a research finding while a missing side is an absence the reader must
interpret.

- **Everything fails conservative.** An unknown SPY trend, a missing regime and
  a stale one all land the short side on `relative_only`. `spy_trend` returns
  **None**, never False, when there is too little history — False would read as
  "below the 200-DMA", which is one of the conditions that CLEARS a directional
  short. Staleness is 96 h: enough for a weekend plus a holiday Monday,
  deliberately not generous, because erring long is the dangerous direction.
- **Longs are never `blocked`** — a long in a downtrend is a worse trade, not a
  forbidden one, and the model still ranks cross-sectionally. Demote, don't block.
- ⚠ **The structural read outranks the fast one, and live verification is what
  taught us that.** The first implementation let a committed downward regime
  clear directional shorts outright. Run against the real tape it returned SPY
  *above a rising 200-DMA* → longs cleared, and *Softening* → shorts cleared:
  both sides cleared, a contradiction on its face. The 200-DMA is a multi-week
  structure while the committed direction comes from a 5-minute EMA slope and a
  15-minute composite, so using the latter to authorise a **twenty-day**
  directional short is the same horizon mismatch the audit criticised in the
  legacy engine. A downward regime may now tip a structure that has stopped
  rising; it may not override one that has not. The regime still shows in the
  reasons either way, as context rather than permission.

### 2.3 Dealer/IV context
- **Test**: a fresh matrix row yields the context; `net_gex == 0.0` (the
  after-hours all-zero-grid signature) or a stale timestamp yields `None`/`"na"`
  and **never** fabricated levels; a symbol absent from the matrix yields
  "not collected".
- **Code**: a pure `dealer_context(row, now)` mirroring `desk._walls_trustworthy`
  + a bus read in `analyze`. Note this is Tier-2 reading a Tier-3 cache — allowed;
  Tier 1 stays engine-free.

### 2.4 Per-symbol snapshot + peers
- **Test**: `build_universe_factor_snapshot` returns `{symbol: {factor: value}}`;
  the flat `{factor: [values]}` basis the scorer consumes is **derived** and
  byte-identical to today's for the same inputs (a characterization test, so the
  scoring path provably does not move); `sector_peers(symbol, snapshot)` ranks
  within the sector and returns nearest neighbours.
- **Code**: change the snapshot shape + a derive helper; cache-version bump so a
  stale-shaped cached payload is not misread.

### 2.5 Contract + page
- **Test**: `TradeAnalysis` accepts the four new optional blocks and validates
  without them (additive); pure row-builders render each block and no-op when
  absent; `test_no_inline_style.py` still covers the page.
- **Code**: contract fields, page builders, `page_help.py` + manual updates.

**Exit:** during RTH a collected megacap shows real dealer levels; off-hours the
same symbol shows the guarded "na" (**verify both live in dev**); a name above
its rising 200-EMA prints a blocked short with reasons.

---

## Phase 3 — the trade ticket

### 3.1 Convention unification (first — it gates the rest)
- **Test** (`shared/tests/test_cross_tier_mirrors.py`): the deep-dive flip
  helper and `gamma_tool.snapshot_summary` agree on a shared fixture grid; a
  source-level test pins that only one 25Δ sign convention exists.
- **Code**: adopt `flow_skew`'s `put − call`; relabel the deep dive's. Port the
  banded + persistence flip as a small pure function into `deepdive/engine.py`
  (it may not import `gamma_tool` — per-domain engine isolation).

### 3.2 Structure matrix
- **Test** (`trade-analyzer/tests/analysis/test_structure.py`, pure): each
  (side, IV state, wall geometry) cell returns the documented structure and a
  30–45 DTE tenor; an unknown IV state degrades to the mid column, never raises.
- **Code**: `src/analysis/structure.py`, module constants until a second tier
  needs them (then a TOML — the house rule for config files).

### 3.3 Trade Plan block
- **Test**: a cleared long yields every field populated; a blocked side yields
  the "no trade — what would change it" form; the time stop always equals the
  artifact's `horizon`; a missing ATR or wall omits that line rather than
  printing a placeholder.
- **Code**: a pure builder + card render + `handoff` wiring to Calculator/paper.

**Exit:** one cleared long and one relative-only short each render a complete
ticket in dev; the long's legs reach the Calculator through the stash.

---

## Phase 4 — model refit (offline, parallel with P2–P3)

Order matters: **measure the noise floor before adding factors**, so a floor
change and a factor change are never confounded in the same OOS number.

1. **Noise-floor study.** Re-fit at `min_abs_ic` ∈ {0.005, 0.01, 0.02} on the
   current universe; report OOS IC, kept-factor count, and whether `rs_spy`'s
   sign flip survives. Adopt only if it wins OOS.
2. **Universe expansion** to ~140–160 liquid optionable names.
3. **Regime classifier** in `src/analysis` (shared by fit and live scorer);
   per-regime weights; `score_symbol` gains a regime selector with `"all"`
   fallback. Test: a missing regime key falls back; the page shows which key scored.
4. **Covariance-aware weighting** (ridge / orthogonalized-residual IC) vs
   signed-IC — keep whichever wins OOS.
5. **Short-factor slate**: MAX effect, downside beta/semivol,
   distance-below-200-EMA. Dealer aggregates enter as *exploratory conditioning*
   only (~2 months of history is too thin to trust). Short interest stays a gate.
6. **Report upgrades**: folds tagged by prevailing regime; long/short split
   stats; per-regime calibration bands.

**Exit:** a new artifact with populated regime keys, and a report that answers —
does it OOS-beat the single-regime fit, do the negative folds cluster where the
classifier says, and is the bottom band's edge real? **If the gate fails, the
Phase-0 artifact stays primary and that outcome is written down.**

---

## Phase 5 — rank board

- **Test**: scoring the snapshot produces one row per symbol with the same
  composite the single-symbol path yields for that symbol (no second code path);
  deciles are computed on the live cross-section; gate-disqualified rows are
  marked, not dropped.
- **Code**: `cache:trade:rank_board` (additive contract) + a board tab. Register
  the route in `test_shell.py` and add the page to `test_no_inline_style.py`.

**Exit:** both deciles render with gates visibly disqualifying rows; an empty
short pool in a strong uptrend renders as "market filter: relative-only".

---

## Phase 6 — feedback loop

- **Labeler + monitor**: nightly fill of realized 5/10/20-day excess returns;
  rolling live IC vs the artifact's OOS IC, **split long/short**; per-symbol
  history on the card; a decay warning beside the staleness banner.
- **Model paper book**: an isolated account (the driver-book pattern) trading
  the P3 structures for cleared top/bottom-band names; results reported
  long/short separately; a third book in the EOD report.
- **Refit cadence**: monthly scheduled `.bat` (the fit stays un-importable by
  services), dated archive, report diff against the prior fit.
- **Investor validation** once `fundamentals_history.db` holds ~2 quarters.

**Exit:** live IC visible and moving; the model book holds positions on both
sides; refits happen without being remembered.
