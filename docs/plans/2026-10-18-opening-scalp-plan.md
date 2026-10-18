# Opening Scalp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save premarket and opening price bars for $SPX, SPY, QQQ and their inputs, measure how often a 0DTE scalp after the first five minutes wins, and show the morning's measured call on a new page.

**Architecture:** Sibling modules in `services/options_svc` (none imports `compute`) keep a SQLite bar store filled from Schwab price history, simulate each morning's four 0DTE trades along the real bars, and score them walk-forward against an alignment count of the operator's premarket inputs. A scheduler slot at 08:36 CT publishes `cache:options:scalp`; a new Tier-1 page reads only that view.

**Tech Stack:** Python 3.11, SQLite, `options-scanner/options_calculator` (the one pricer), `shared.config_toml`, `shared.bus`, pydantic contracts, NiceGUI page kit, pytest.

**Spec:** [docs/plans/2026-10-18-opening-scalp-design.md](2026-10-18-opening-scalp-design.md) — read it first; this plan argues from it.

## Global Constraints

- `$PY` below means the main checkout's venv: `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` on Windows, `/home/administrator/dev/.venv/bin/python` on the VPS. Service suites run from the repo root (`$PY -m pytest services/options_svc/tests/<file>`); webgui tests run from inside `webgui/`.
- New service code imports **nothing from `services/options_svc/compute.py`** (its line count may only fall).
- Every tunable lives in `config/scalp.toml` (new), read through `shared/config_toml.toml_loader`, with built-in defaults for a missing file, key or bad value, never raising; every key catalogued in `webgui/config_schema.py`. Slot times live in `config/sessions.toml`.
- Defaults (verbatim from the spec): `target_pct = 50`, `stop_pct = 50`, `time_stop_min = 60`, `otm_steps = 0`, widths `$SPX 5`, `SPY 1`, `QQQ 1`, `iv_scale = 1.0`, study `iv_scales = [0.8, 1.0, 1.25, 1.5]`, call slot `08:36`, bars slot `16:20`.
- A missing input is ABSENT (`None`), never `0.0` and never "agrees".
- A timezone-naive datetime means CENTRAL. Schwab candle `datetime` is epoch **ms UTC, bar START**; store epoch seconds and convert with `zoneinfo("America/Chicago")`. Time to expiry only through `options_calculator.expiry_time_to_years`.
- Proxy calls go through `services/_proxy.schwab_py_client`; **never** `priority=True` (the one-minute poll's lane).
- Dated price history goes through the proxy's existing `/passthrough` (`endpoint=/pricehistory`, comma-joined `k=v` params). **No proxy change** — so nothing here is a feed file and promotes are not window-restricted by this work.
- Tests never open a live store: every store function takes `db_path=None` resolved at call time; tests pass `tmp_path`.
- A guard of 15+ lines that swallows an exception calls `_degrade.degraded("options.scalp_<area>")`.
- Write test files with the editor, not a heredoc.
- Commit after every task with the attribution line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A shortened session** (the day after Thanksgiving, Christmas Eve): settlement is 12:00 CT, so 0DTE values computed to 15:00 are wrong — the study must skip a date whose last $SPX regular bar starts before 14:55 CT. Test in Task 4.
2. **Monday and post-holiday mornings:** "Asia" begins at the first futures bar after the PRIOR TRADING DAY's 16:00 CT (Sunday 17:00 on a Monday), not at yesterday 17:00. Test in Task 5.
3. **A missing opening bar** (Schwab hole, outage): that symbol gets no direction and no call, the others still do, nothing raises. Tests in Tasks 5 and 8.
4. **A week that crosses a daylight-saving change** (2026-11-01): CT conversion via zoneinfo keeps 08:30 CT at 08:30 on both sides. Test in Task 2.
5. **The 08:36 chain fetch fails:** the call is still published with modelled point distances and `chain: null`, and grading falls back to the model only. Test in Task 8.

---

## Phase 1 — start saving history (ship and promote as soon as Tasks 1–3 are green)

### Task 1: Config and the bar store

**Files:**
- Create: `config/scalp.toml`, `shared/scalp_config.py`, `services/options_svc/scalp_store.py`
- Modify: `repo_paths.py` (add `SCALP_TOML = REPO_ROOT / "config" / "scalp.toml"` and `SCALP_DB = REPO_ROOT / "services" / "options_svc" / "data" / "scalp.db"`), `webgui/config_schema.py` (a `scalp.toml` group cataloguing every key)
- Test: `shared/tests/test_scalp_config.py`, `services/options_svc/tests/test_scalp_store.py`

**Interfaces:**
- Produces (`shared/scalp_config.py`, stdlib + `shared.config_toml` + `repo_paths` only, modelled on `shared/feed_config.py`):
  - `load()`, `reset_cache()`
  - `symbols() -> list[str]` → `["$SPX", "SPY", "QQQ"]`
  - `bar_symbols() -> list[str]` → `["$SPX","SPY","QQQ","/ES","/NQ","IGV","SMH","EWY","XLE","XLK","XLV","$VIX","$VXN"]`
  - `inputs_for(symbol) -> dict` → `{"futures": "/ES"|"/NQ", "etfs": [...], "vol": "$VIX"|"$VXN"}`; SPY and $SPX → `/ES`, `["XLE","XLK","XLV"]`, `$VIX`; QQQ → `/NQ`, `["IGV","SMH","EWY","XLK"]`, `$VXN`
  - `strike_step(symbol) -> float` ($SPX 5, SPY 1, QQQ 1), `width(symbol) -> float` ($SPX 5, SPY 1, QQQ 1)
  - `otm_steps() -> int` (0), `target_pct() -> float` (50), `stop_pct() -> float` (50), `time_stop_min() -> int` (60), `iv_scale() -> float` (1.0), `iv_scales() -> list[float]` ([0.8,1.0,1.25,1.5])
  - `dead_zone_pct() -> float` (0.05, percent, for returns), `dead_zone_frac() -> float` (0.1, for 0..1 positions), `open_dead_zone_pct() -> float` (0.03), `buckets() -> list[float]` ([0.4, 0.7] — fraction-agreeing cut points), `min_samples() -> int` (20), `min_premium() -> float` (0.05), `chain_strikes() -> int` (10), `news_items() -> int` (5), `scorecard_days() -> int` (20)
- Produces (`scalp_store.py`):
  - `connect(db_path=None) -> sqlite3.Connection` (creates schema; WAL)
  - `upsert_bars(rows: list[tuple], db_path=None) -> int` — rows `(symbol, interval, ts, open, high, low, close, volume)`, `INSERT OR REPLACE`, returns count
  - `bars(symbol, interval, start_ts, end_ts, db_path=None) -> list[Bar]` — `Bar` a NamedTuple `(ts, open, high, low, close, volume)`, ascending, `start_ts <= ts < end_ts`
  - `latest_ts(symbol, interval, db_path=None) -> int | None`
  - `save_chain_slice(day: str, symbol: str, ts: int, body: dict, db_path=None)` (zlib JSON BLOB) and `chain_slice(day, symbol, db_path=None) -> dict | None`
  - Backup: `*.db` is gitignored and `tools/backup_local.db_paths` sweeps every non-empty store not in `DB_EXCLUDE`, so `scalp.db` is in the nightly backup with no change — do NOT add it to `DB_EXCLUDE`.
  - Schema: `bars(symbol TEXT, interval INTEGER, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(symbol, interval, ts))`, `chain_slices(day TEXT, symbol TEXT, ts INTEGER, body BLOB, PRIMARY KEY(day, symbol))`. Later tasks add their tables through `connect`.

- [ ] **Step 1: Write the failing tests**

```python
# shared/tests/test_scalp_config.py
def test_defaults_when_file_missing(monkeypatch, tmp_path): ...
    # point the loader at a missing path; assert target_pct()==50, stop_pct()==50,
    # time_stop_min()==60, width("$SPX")==5, width("SPY")==1, iv_scales()==[0.8,1.0,1.25,1.5]
def test_bad_values_fall_back(...):   # target_pct = "x", buckets = [0.9, 0.1] -> defaults
def test_inputs_for_qqq_reads_nq_and_tech_etfs(): assert inputs_for("QQQ")["etfs"] == ["IGV","SMH","EWY","XLK"]
```

```python
# services/options_svc/tests/test_scalp_store.py
def test_upsert_is_idempotent(tmp_path):      # same row twice -> one row, second write wins
def test_bars_range_is_half_open_and_sorted(tmp_path)
def test_chain_slice_round_trips(tmp_path)
def test_no_default_path_bound_at_def():      # inspect.signature(...).parameters["db_path"].default is None
```

- [ ] **Step 2: Run them; expect FAIL (modules missing)**

Run: `$PY -m pytest shared/tests/test_scalp_config.py services/options_svc/tests/test_scalp_store.py -v`

- [ ] **Step 3: Implement** the two modules and `config/scalp.toml` (every key with a comment saying what it does, as `config/feed.toml` does), the `repo_paths` constants, and the `config_schema.py` group.

- [ ] **Step 4: Run them plus the catalogue test; expect PASS**

Run: `$PY -m pytest shared/tests/test_scalp_config.py services/options_svc/tests/test_scalp_store.py -v` then `cd webgui && $PY -m pytest tests/test_config_schema.py -q`

- [ ] **Step 5: Commit** `feat(scalp): config and bar store`

### Task 2: Fetching bars — backfill and the nightly top-up

**Files:**
- Create: `services/options_svc/scalp_fetch.py`, `tools/scalp_backfill.py` (CLI: `python tools/scalp_backfill.py` runs `backfill()` once on the box)
- Modify: `config/sessions.toml` (`[slots.scalp_bars]` `grace_min = 120`, `close = "16:20"` with a comment on why: after the 16:00 flow-delta report and the 15:20 collection stop), `services/options_svc/scheduler.py` (`scalp_bars_due(now, ran_slots)` mirroring `income_slot_due`; a branch running `scalp_fetch.top_up` in the executor, latched before it runs)
- Test: `services/options_svc/tests/test_scalp_fetch.py`, `services/options_svc/tests/test_scheduler.py` (add)

**Interfaces:**
- Consumes: Task 1's `upsert_bars`, `latest_ts`, `scalp_config.bar_symbols`
- Produces:
  - `candles_to_rows(symbol: str, interval: int, payload: dict) -> list[tuple]` — Schwab `{"candles":[{"datetime": ms, ...}]}` → store rows, `ts = ms // 1000`; drops a candle with any non-finite OHLC
  - `fetch_range(symbol, interval, start_ms, end_ms, client=None) -> list[tuple]` — one `/passthrough` call with `symbol, frequencyType=minute, frequency=<interval>, startDate, endDate, needExtendedHoursData=true`; returns `[]` on any failure (logged)
  - `backfill(db_path=None, client=None, today=None) -> dict[str, int]` — per symbol and interval, walk BACK from today in windows (1-min: 10 days, 5-min: 30 days — each under the 40,000-candle cap) until two consecutive empty windows; returns rows written per `"SYMBOL/interval"`
  - `top_up(db_path=None, client=None, today=None) -> dict[str, int]` — from `latest_ts` (or 3 trading days back if none) to now, both intervals, every bar symbol
  - `ct(ts: int) -> datetime` — epoch seconds → aware CT datetime (used by every later task)

- [ ] **Step 1: Write the failing tests**

```python
def test_candles_to_rows_converts_ms_and_drops_nan()
def test_backfill_stops_after_two_empty_windows()       # fake client returns 3 windows then empties; assert calls stop
def test_backfill_windows_stay_under_candle_cap()       # 1-min windows span <= 10 days, 5-min <= 30
def test_fetch_range_never_uses_priority_lane()          # fake client records priority kw; never True
def test_top_up_starts_at_latest_ts()
def test_ct_keeps_0830_across_dst():                     # 2026-10-30 13:30Z -> 08:30 CT; 2026-11-02 14:30Z -> 08:30 CT
def test_scalp_bars_due_once_per_day_inside_grace()     # in test_scheduler.py
```

- [ ] **Step 2: Run; expect FAIL**

Run: `$PY -m pytest services/options_svc/tests/test_scalp_fetch.py -v`

- [ ] **Step 3: Implement.** `fetch_range` calls `client._get("/passthrough", params={"endpoint": "/pricehistory", "params": "symbol=...,frequencyType=minute,..."})` on `services._proxy.schwab_py_client` (inject `client` for tests).

- [ ] **Step 4: Run the new tests and the whole options_svc scheduler test file; expect PASS**

- [ ] **Step 5: Commit** `feat(scalp): bar backfill and nightly top-up`

### Task 3: The 08:36 chain capture

**Files:**
- Create: `services/options_svc/scalp_chain.py`
- Modify: `config/sessions.toml` (`[slots.scalp]` `grace_min = 20`, `call = "08:36"`, comment: not 08:35 — the autoscan fetches :32–:35 and Schwab refuses the first minute after the half hour; check the proxy access log before moving it), `services/options_svc/scheduler.py` (`scalp_call_due(now, ran_slots)`; in Phase 1 the branch runs only `scalp_chain.capture_all`, Task 8 widens it)
- Test: `services/options_svc/tests/test_scalp_chain.py`

**Interfaces:**
- Produces:
  - `near_money_slice(chain: dict, spot: float, n: int) -> dict` — keep the `n` strikes either side of spot from both `callExpDateMap` and `putExpDateMap`, same nested shape; every contract keeps `bid, ask, mark, delta, gamma, theta, vega, volatility, totalVolume, openInterest`
  - `capture(symbol, day, client=None, db_path=None) -> dict | None` — `get_option_chain(symbol, from_date=day, to_date=day)` (0DTE only, normal lane), slice with `chain_strikes()`, save via `save_chain_slice`; returns the slice or `None` on failure
  - `capture_all(day=None, client=None, db_path=None) -> dict[str, dict | None]` for `scalp_config.symbols()`
  - `atm_iv(slice_: dict, spot: float) -> float | None` — mean of the at-the-money call and put `volatility` / 100; `None` if either is unusable (Schwab's `-999` is unusable)

- [ ] **Step 1: Failing tests:** `test_slice_keeps_n_each_side`, `test_capture_saves_and_returns_slice`, `test_capture_failure_returns_none_and_saves_nothing`, `test_atm_iv_refuses_minus_999`, `test_scalp_call_due_fires_at_0836_not_0835`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run options_svc suite (`$PY -m pytest services/options_svc/tests -q`); expect the failing SET unchanged from before the task plus none new.**
- [ ] **Step 5: Commit** `feat(scalp): capture the three 0DTE chains at 08:36`

### Phase 1 checkpoint (operator)

Merge to `main`, push, promote **outside 06:30–15:20 CT** (`ssh vps2 'cd /home/administrator/dev && tools/promote.sh'`), then on the box run `tools/scalp_backfill.py` once and report rows per symbol/interval. Verify the next morning that `chain_slices` has three rows dated that day.

---

## Phase 2 — the study

### Task 4: Simulating one trade along the bars

**Files:**
- Create: `services/options_svc/scalp_sim.py`
- Test: `services/options_svc/tests/test_scalp_sim.py`

**Interfaces:**
- Consumes: `options_calculator.bs_price`, `expiry_time_to_years`, `RISK_FREE_RATE` (import as other options_svc modules do: `import options_calculator as oc`); Task 2's `ct`
- Produces:
  - `ROLES = ("long_with", "long_against", "credit_with", "credit_against")`
  - `structure_for(role: str, direction: int) -> str` — `+1`: long_with→`"long_call"`, long_against→`"long_put"`, credit_with→`"pcs"`, credit_against→`"ccs"`; `-1` mirrors
  - `strikes_for(structure, spot, step, width, otm_steps) -> tuple[float, float | None]` — long call `ceil(spot/step)*step + otm_steps*step`; long put `floor(...) - otm_steps*step`; PCS short `floor(spot/step)*step - otm_steps*step`, long `short - width`; CCS mirror with `ceil` and `+ width`. Returns `(short_or_long, long_leg_or_None)`
  - `value(structure, spot, strikes, when: datetime, day: date, sigma: float) -> float` — long: the option; credit: short − long (a positive cost to close)
  - `Outcome` NamedTuple `(result: str, minutes: int | None, entry_value: float, end_pnl_pct: float | None, both_in_bar: bool)`, `result` ∈ `"win","loss","timeout","skip"`
  - `simulate(structure, spot0, strikes, entry: datetime, day: date, sigma: float, path: list[Bar], interval: int) -> Outcome`
  - `level_points(structure, spot0, strikes, entry, day, sigma) -> tuple[float | None, float | None]` — underlying points (signed: + up) at which the structure reaches its win and its loss level at entry time, by bisection within ±10% of spot; `None` when not reachable
  - `short_session(day_bars_spx: list[Bar]) -> bool` — True when the last regular-session $SPX bar starts before 14:55 CT

**Rules the tests pin (spec §3, §5.1):**
- Long: win when value ≥ `entry × (1 + target_pct/100)`, loss when ≤ `entry × (1 − stop_pct/100)`. Credit: win when cost to close ≤ `credit × 0.5`, loss when ≥ `credit × 1.5`.
- Per bar, value at the bar's END time, priced at both the bar's high and low. A bar reaching both levels → `"loss"`, `both_in_bar=True`.
- Path = bars whose start is in `[entry, entry + time_stop_min)`; neither level → `"timeout"`, `end_pnl_pct` from the last bar's close.
- `entry_value < min_premium()` → `"skip"`.

- [ ] **Step 1: Failing tests:** `test_long_call_win_first`, `test_long_call_loss_first`, `test_both_levels_in_one_bar_is_loss`, `test_timeout_after_60_minutes`, `test_pcs_win_at_half_credit`, `test_pcs_stop_at_one_and_half_credit`, `test_strikes_pcs_short_at_or_below_spot` (spot 571.4, step 1 → short 571, long 570), `test_strikes_ccs_short_at_or_above_spot` (→ 572, 573), `test_structure_mirrors_on_down_morning`, `test_level_points_signs` (long call: win point > 0, loss point < 0), `test_tiny_premium_skips`, `test_short_session_detected` (last bar 11:55 CT → True).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit** `feat(scalp): simulate a 0DTE scalp along the bars`

### Task 5: The morning's inputs

**Files:**
- Create: `services/options_svc/scalp_inputs.py`
- Test: `services/options_svc/tests/test_scalp_inputs.py`

**Interfaces:**
- Consumes: Task 1 `bars`, `inputs_for`; Task 2 `ct`; `shared.market_calendar.prev_trading_day`, `regular_open_on`
- Produces:
  - `Morning` dataclass: `day, symbol, direction (+1|-1|None), entry_spot, entry_ts, open_bar (Bar|None), inputs: dict[str, float | None], agree: dict[str, int | None]` (+1 agrees, −1 disagrees, 0 dead zone, None absent), `vol_at_entry: float | None`
  - `morning(symbol: str, day: date, interval: int = 5, db_path=None) -> Morning`
  - `alignment(m: Morning) -> float | None` — agreeing / (agreeing + disagreeing); `None` when the denominator is 0 or direction is None

**Definitions (all CT; "prior close" = close of the 5-min bar starting 14:55 on `prev_trading_day(day)`):**

| Input key | Value | Agrees with up when |
|---|---|---|
| `fut_asia` | futures close at last bar starting < 02:00 ÷ open of first bar after prior trading day 16:00 − 1, in % | > +dead_zone_pct |
| `fut_europe` | last bar < 08:30 close ÷ last bar < 02:00 close − 1, % | > +dead_zone_pct |
| `fut_gap` | last bar < 08:30 close ÷ futures prior close − 1, % | > +dead_zone_pct |
| `fut_range_pos` | (last < 08:30 close − overnight low) ÷ (overnight high − low), overnight = first bar after prior 16:00 … < 08:30 | > 0.5 + dead_zone_frac |
| `etf:<SYM>` each | last premarket bar (03:00 ≤ start < 08:30) close ÷ prior close − 1, %; no premarket bar → None | > +dead_zone_pct |
| `vol_change` | $VIX last < 08:30 close ÷ prior close − 1, % ($VIX for every symbol: $VXN has no premarket) | < −dead_zone_pct (inverted) |
| `open_close_loc` | (open bar close − low) ÷ (high − low) | > 0.5 + dead_zone_frac |
| `open_broke_pm` | open bar high > premarket high (+1) / low < premarket low (−1) / neither (0); for $SPX the premarket and the open bar are the futures' | sign |

- `direction` = sign of (open bar close − open bar open) when its size ≥ `open_dead_zone_pct`% of open, else `None`. The open bar is the symbol's own 5-min bar starting 08:30 (for 1-min, 08:30–08:34 aggregated). Disagreement mirrors agreement for a down direction.
- `vol_at_entry` = close of the symbol's `inputs_for(symbol)["vol"]` bar starting 08:30, / 100; QQQ falls back to `$VIX` when `$VXN` is absent (recorded in `inputs["vol_source"]`).
- `open_range_ratio` (open bar range ÷ mean open-bar range over the prior 20 sessions) is recorded in `inputs` but is **not** an agreement input (it has no direction).

- [ ] **Step 1: Failing tests** on hand-built bars in a tmp store: `test_up_morning_all_agree_alignment_one`, `test_dead_zone_counts_neither`, `test_missing_etf_is_absent_not_disagree`, `test_vix_rise_disagrees_with_up`, `test_monday_asia_starts_sunday_evening` (prior trading day Friday; first futures bar Sunday 17:00), `test_missing_open_bar_gives_no_direction`, `test_spx_reads_futures_for_premarket`.
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** `feat(scalp): the morning's measured inputs`

### Task 6: Outcomes table, walk-forward model and the study report

**Files:**
- Create: `services/options_svc/scalp_model.py`, `services/options_svc/scalp_study.py`, `tools/scalp_study_report.py`
- Test: `services/options_svc/tests/test_scalp_model.py`, `services/options_svc/tests/test_scalp_study.py`

**Interfaces:**
- Consumes: Tasks 4–5
- Produces (`scalp_model.py`, pure):
  - `bucket_of(alignment: float | None, cuts: list[float]) -> str | None` — `"low"` < cuts[0] ≤ `"mid"` < cuts[1] ≤ `"high"`
  - `wilson(wins: int, n: int, z=1.96) -> tuple[float, float]`
  - `walk_forward(rows: list[dict]) -> list[dict]` — `rows` sorted by day, each `{day, bucket, win: 0|1}` for ONE (symbol, role, iv_scale); returns per row `{day, p_bucket, n_bucket, p_base, n_base}` computed ONLY from earlier rows (None until any exist)
  - `verdict(scored: list[dict], rows, min_samples) -> dict[str, dict]` — per bucket `{brier_bucket, brier_base, n_scored, edge: bool}`; `edge` iff `n_scored ≥ min_samples` and `brier_bucket < brier_base`
  - `edge_across_scales(verdicts_by_scale: dict[float, dict]) -> dict[str, bool]` — a bucket's edge only if true at every scale
- Produces (`scalp_study.py`):
  - Tables (via `scalp_store.connect`): `outcomes(day, symbol, iv_scale, role, structure, bucket, result, minutes, entry_value, end_pnl_pct, both_in_bar, interval, PRIMARY KEY(day, symbol, iv_scale, role, interval))`, `mornings(day, symbol, json)`, `verdicts(symbol, role, bucket, json, PRIMARY KEY(symbol, role, bucket))`
  - `rebuild(db_path=None) -> dict` — every stored day with a direction and not `short_session`, every symbol, role, and `iv_scales()`, on **5-min** bars; additionally on 1-min bars at `iv_scale()` for days that have them (the bias check); all in ONE transaction (delete + insert). Returns counts.
  - `current_rates(symbol, bucket, db_path=None) -> dict[str, dict]` — per role over ALL stored days at `iv_scale()`: `{p, lo, hi, n, p_base, n_base, edge}` (edge from `verdicts`)
- `tools/scalp_study_report.py` prints Markdown: per symbol × role × bucket the walk-forward Brier vs base at each scale, n, edge; up-only and down-only rates (diagnostic); pooled-symbols rates (diagnostic); 5-min vs 1-min result on overlapping days and the `both_in_bar` share.

- [ ] **Step 1: Failing tests:**

```python
def test_walk_forward_never_uses_same_or_later_day():
    rows = [{"day": f"2026-09-{d:02d}", "bucket": "high", "win": 1} for d in range(1, 6)]
    out = walk_forward(rows)
    assert out[0]["p_bucket"] is None and out[0]["n_bucket"] == 0
    assert out[2]["n_bucket"] == 2
def test_edge_needs_min_samples()
def test_edge_needs_lower_brier_than_base()
def test_edge_must_hold_at_every_scale()
def test_wilson_bounds_contain_p()
def test_rebuild_is_deterministic(tmp_path)      # rebuild twice -> identical outcomes rows
def test_rebuild_skips_short_sessions(tmp_path)
def test_rebuild_verdict_uses_5min_only(tmp_path) # a 1-min row never enters verdicts
```

- [ ] **Step 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5:** add the nightly `scalp_study.rebuild` after `top_up` in the `scalp_bars` branch (Task 2's scheduler branch). Commit `feat(scalp): outcomes, walk-forward model and study report`.

### Phase 2 checkpoint (operator) — STOP HERE

Promote; on the box run `tools/scalp_study_report.py` and write its output, with a plain-language reading, to `docs/reference/opening-scalp-study.md` (dated). **Report the verdict to the operator before starting Phase 3.** If no bucket shows an edge at any symbol, say so plainly and ask whether the page is still wanted. Do not tune buckets, dead zones or inputs after reading the result; any change is a new, declared study.

---

## Phase 3 — the morning call and the page

### Task 7: The view contract

**Files:**
- Modify: `shared/contracts/options.py` (add `ScalpView(_MoneyView)`; export it like its neighbours)
- Test: `shared/contracts/tests/test_scalp_view.py`

**Interfaces:**
- Produces: `ScalpView` fields (all defaulted): `day: str | None`, `generated_at: str | None`, `state: str` (`"call"`, `"waiting"`, `"closed"`, `"error"`), `symbols: dict[str, dict]`, `context: dict`, `scorecard: dict`. Per symbol: `direction: int | None`, `bucket: str | None`, `alignment: FiniteFloat | None`, `inputs: dict`, `agree: dict`, `roles: dict[str, dict]` (each `{structure, p, lo, hi, n, p_base, n_base, edge, strikes, entry_value, win_points, loss_points}`), `chain: dict | None`, `highlight: str | None`, `note: str | None`. Validator: every `p`, `lo`, `hi`, `p_base` present is finite and within [0, 1].

- [ ] Steps: failing tests (`test_probability_outside_unit_interval_refused`, `test_nan_refused`, `test_minimal_payload_validates`) → implement → PASS → commit `feat(scalp): view contract`.

### Task 8: Building and publishing the call; the refresh command

**Files:**
- Create: `services/options_svc/scalp_call.py`
- Modify: `services/options_svc/scheduler.py` (the `scalp` branch now runs `scalp_call.run(bus)`), `services/options_svc/handlers.py` (`@_command("scalp_refresh") _cmd_scalp_refresh`; add to `_REPLAY_GUARDED` and `SLOW_LANE`; a `_STALE_WHY` line)
- Test: `services/options_svc/tests/test_scalp_call.py`, a row in the existing handler-table test if one enumerates commands

**Interfaces:**
- Consumes: Tasks 2–7
- Produces:
  - `VIEW = "cache:options:scalp"`, `EVENT = "events:options:scalp"`
  - `build(day: date, now: datetime, db_path=None, client=None) -> dict` — `top_up` for today; `capture_all`; per symbol `morning()`, bucket, `current_rates()`, strikes and `level_points()` with `sigma = atm_iv(slice) or vol_at_entry × iv_scale()`, live `entry_value` from the chain's mid when the slice has the strike; `highlight` = the role with `edge` and the highest `p` (None if no edge: `note = "no measured edge"`). Saves the payload in `calls(day, symbol, json)`.
  - `run(bus, now=None, db_path=None, client=None) -> None` — before 08:35 CT or on a non-trading day publish `state="waiting"`/`"closed"` with yesterday's call kept under `symbols`; else build, validate with `ScalpView` (invalid → not published, `_degrade.degraded("options.scalp_view")`), `bus.cache_set(VIEW, payload, event=EVENT)`
  - `_cmd_scalp_refresh(bus, command)` → `scalp_call.run(bus)`
  - `context` block: `report_url` (`https://neuralstrike.co/report.html`, config `report_url`), `news` (top `news_items()` from `cache:news:feed` whose tickers include SPY, QQQ, SPX or ES, else the newest), `flow` (today's `cache:options:flow_alerts` entries for the three symbols since 08:30). Read with `bus.cache_get(...).payload`.

- [ ] **Step 1: Failing tests:** `test_waiting_before_0835`, `test_closed_on_holiday`, `test_missing_open_bar_symbol_has_note_others_called` (Review Focus 3), `test_chain_failure_still_publishes_with_model_points` (Review Focus 5: `chain is None`, `win_points` present), `test_no_edge_sets_note_and_no_highlight`, `test_invalid_payload_not_published`, `test_refresh_is_replay_guarded_and_slow_lane`, `test_scalp_modules_do_not_import_compute` (AST check over every `scalp_*.py`).
- [ ] **Step 2–4:** run (FAIL), implement, run (PASS); then the whole options_svc suite — failing SET unchanged.
- [ ] **Step 5: Commit** `feat(scalp): publish the morning call`

### Task 9: The page

**Files:**
- Create: `webgui/pages/options/scalp.py`
- Modify: `webgui/main.py` (`OPTIONS_RAIL` gains `("/options/scalp", "Opening Scalp", "timer")` after Flow Alerts — a rail entry, not the Options tab strip, which already holds nine tabs; an `@_page("/options/scalp")` inside `_layout`; a colour in the route-colour map), `webgui/page_help.py` (entry), `webgui/tests/test_shell.py` (route; drawer count), `webgui/tests/test_no_inline_style.py` (the file)
- Test: `webgui/tests/test_scalp_page.py`

**Interfaces:**
- Consumes: `cache:options:scalp` only (`bus_client.read`, `watch_view`), `pages.fmt`, `pages.ui_kit`; enqueues `scalp_refresh` on `cmd:options` the way other pages enqueue a command
- Produces: `render()`; pure helpers `role_rows(symbol_payload) -> list[dict]` and `headline(symbol_payload) -> str`

**What it shows (spec §6.4):** per symbol a card — the opening direction in words ("opened up"), alignment bucket, then a `kit.table` of the four roles named by that morning's contract (`fmt.pct` for p, interval, base rate; n as a count; strikes via `fmt.strike`; `win_points`/`loss_points` via `fmt.price`), the highlighted row marked, or the note "no measured edge". An inputs list with agree/disagree/absent per input. A context panel titled "Not in the probability" (report link, headlines, flow alerts). A scorecard table (Task 10). A `waiting`/`closed` state shows "Next call at 08:36 CT" above yesterday's call. A Refresh `kit.button`.

- [ ] **Step 1: Failing tests:** `test_role_rows_name_contracts_by_direction` (down morning → `long_with` reads "Long put"), `test_absent_input_shows_absent_not_disagree`, `test_no_edge_note_shown`, `test_page_reads_only_scalp_view` (source check: the only cache key spelled is `cache:options:scalp`), `test_probabilities_print_two_decimals_via_fmt`.
- [ ] **Step 2–4:** run (FAIL), implement, run `cd webgui && $PY -m pytest tests/test_scalp_page.py tests/test_shell.py tests/test_no_inline_style.py tests/test_ui_kit_guard.py -q` (PASS).
- [ ] **Step 5:** render it in `tools/ui_harness.py` on a fake bus with a hand-built payload (loopback; stop the harness after), take a screenshot, and check it at phone width. Commit `feat(scalp): Opening Scalp page`.

### Task 10: Grading and the scorecard

**Files:**
- Modify: `services/options_svc/scalp_study.py` (`grade_day`), `services/options_svc/scalp_call.py` (fill `scorecard`), `webgui/pages/options/scalp.py` (scorecard table)
- Test: `services/options_svc/tests/test_scalp_grade.py`

**Interfaces:**
- Produces: table `grades(day, symbol, role, structure, model_result, chain_result, highlighted INTEGER, PRIMARY KEY(day, symbol, role))`; `grade_day(day, db_path=None) -> int` — for every role (not only the highlighted one): `model_result` via `simulate` at `iv_scale()` on 5-min bars; `chain_result` via `simulate` with `sigma` implied from the saved slice's mid for those strikes (`oc.implied_vol`), `None` without a slice; also stores the day's `atm_iv ÷ vol_at_entry` ratio (the `iv_scale` calibration) in `mornings`. Run nightly after `rebuild`.
- `scorecard` in the view: last `scorecard_days()` graded days per symbol; headline = highlighted role's wins / highlighted days beside that role's base rate; an all-roles table.

- [ ] Steps: failing tests (`test_grades_every_role_not_only_highlighted`, `test_chain_result_none_without_slice`, `test_scorecard_headline_counts_highlighted_days_only`) → implement → PASS → commit `feat(scalp): grade each day and show the scorecard`.

### Task 11: Documentation

**Files:** `docs/CHANGELOG.md`, `docs/webgui-routes.md` (`/options/scalp`), `docs/manuals/` User Guide and Reference Guide (the page) and Technical Reference (how the probability is computed: §5 of the spec in plain words), `webgui/page_help.py` (checked against the shipped behaviour), `docs/manuals/diagrams/make_diagrams.py` only if a diagram shows options_svc's jobs. CLAUDE.md: one line in the folder map for `services/options_svc/data/scalp.db` ("kept forever; history Schwab no longer serves") — nothing else.

- [ ] Run `$PY build_docs.py` (or the repo's documented manual build) and the manuals' tests; commit `docs(scalp): Opening Scalp`.

### Final

- [ ] Whole suites: options_svc, shared, webgui — failing SETs unchanged from `main`.
- [ ] Promote outside 06:30–15:20 CT; next trading morning, verify `cache:options:scalp` published at 08:36 with three symbols, and the page renders it.
