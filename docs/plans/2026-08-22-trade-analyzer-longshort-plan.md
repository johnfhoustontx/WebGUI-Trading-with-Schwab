# Trade Analyzer long/short — implementation plan

Companion to [`2026-08-22-trade-analyzer-longshort-design.md`](2026-08-22-trade-analyzer-longshort-design.md).
TDD throughout: write the failing test, watch it fail for the right reason, then
the minimal code. Every phase ends with the suite green, verified running in
dev, docs + `page_help.py` updated in the same commit, then `promote.bat`.

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

### 1.2 finviz supplement
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

### 1.4 Start the accrual stores
- **Test**: `fundamentals_history` and `rec_journal` create their schema
  idempotently, insert, and read back; a failed write never raises into
  `analyze` (mirror `iv_history`'s contract).
- **Code**: two small modules + call sites in `analyze`. Nightly universe sweep
  for fundamentals; one row per analyze for the journal. **Isolate the DB paths
  under pytest** — the documented "pytest must isolate on-disk stores" trap.

**Exit:** live analyze shows `days_to_earnings` populated and the earnings gate
firing; Investor valuation ≠ 0 for a symbol with a known P/E; both stores
accruing; prod dry-run names only `finvizfinance`.

---

## Phase 2 — both sides + context

### 2.1 Mirrored + short gates
- **Test** (`test_recommendation_position.py`): above a rising 200-EMA caps
  SELL at HOLD; a sector in confirmed **up**trend caps SELL; SI > threshold or
  DTC > threshold caps SELL **and prints the reason**; none of the three touches
  a BUY. Existing long-side gate tests must stay green.
- **Code**: `PositionVerdict.score` gains the mirrored branches;
  `SectorStrength` gains `in_confirmed_uptrend`.

### 2.2 Direction clearance
- **Test** (`services/trade_svc/tests/test_compute.py`, pure helper): SPY above
  a rising 200-DMA + neutral committed trend → long cleared, short
  relative-only; SPY below + Softening → short cleared; a regime payload older
  than one session → `"unknown"` → short relative-only (**conservative**, never
  cleared); both sides always present in the output with reasons.
- **Code**: a pure `direction_clearance(spy_close, regime, now)` +
  a staleness-guarded bus read in `analyze`.

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
