# /ES and /NQ 0-DTE chains on Dealer Positioning: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put /ES and /NQ on the private Dealer Positioning page, computed only from their own 0-DTE option chains read out of ThinkorSwim on the operator's PC.

**Architecture:** A PowerShell capture on the PC reads contracts through ThinkorSwim's Excel link and sends raw values over `ssh vps2` to a CLI that writes `cmd:futures_chain`. An `options_svc` consumer validates each snapshot, calculates gamma, builds a Schwab-shaped chain, writes it through `derive_minute`, and publishes through the existing gamma cache view. The PowerShell stays dumb: every rule that can be unit-tested (contract names, expiry checks, sessions) lives in Python on the server, and the PC asks for a plan.

**Tech Stack:** Python 3.11, FastAPI service scaffold, Redis Streams, SQLite (`gex_history.db`), NiceGUI + Highcharts, Windows PowerShell 5.1 + Excel COM + ThinkorSwim RTD, OpenSSH.

**Spec:** [2026-10-31-futures-0dte-chain-feed-design.md](2026-10-31-futures-0dte-chain-feed-design.md)

**Where this plan departs from a first reading of the spec** (the spec was corrected in place on the same day):
- No hand-kept expiry table. A new probe showed each contract's `DESCRIPTION` states its underlying future, multiplier, expiry date and `(AM Settled)`. These are read and re-checked server-side on every snapshot.
- The Gamma Archive leaves these symbols out in the first version: it closes a calendar date at the stock session's stop, which cannot hold a session spanning two dates.
- The Flow tab is hidden for these symbols alongside Term, because it prints contract counts, which the adapter scales. This keeps the raw-count problem of spec 5.4 to one place. Net Prem is symbol-independent and stays.
- The projected afternoon band appears only from the expiry day's stock open; `forward_marks` knows only that window, and a wrong band overnight is worse than none.

## Global Constraints

- Internal symbols are `FOP.ES` and `FOP.NQ`; the page labels them `/ES` and `/NQ`. `shared/symbols.SYMBOL_RE` is not edited.
- Any /ES or /NQ number coming from `$SPX` or `$NDX`, including as a fallback, is a failure. A stale snapshot must never read as current.
- No change to any number of an existing symbol. Every engine change is an optional argument defaulting to today's behaviour.
- These symbols never reach Schwab, the proxy, `gex_collector.collection_symbols()`, or any public key.
- A value that can be absent is absent, never 0: an unusable implied volatility means no gamma.
- Contract size: the adapter multiplies open interest and volume by `multiplier / 100` (0.5 and 0.2). `gamma_tool.py` is not edited for size.
- `services/options_svc/compute.py` may not grow past `COMPUTE_MAX_LINES` (10487; it is 10459). New service code goes in sibling modules that import nothing from `compute`. `gamma.render` has a size ceiling too: new page code goes in a sibling module.
- Every tunable lives in `config/futures_chain.toml`, with a `webgui/config_schema.py` entry. The interval is one value and nothing assumes 5 minutes.
- The capture owns a private hidden Excel started with `EXCEL.EXE /automation -Embedding`, stops it by process id, never touches the operator's Excel, never starts ThinkorSwim, never enters a password, never places or prepares an order.
- Binds stay `127.0.0.1`. No secrets committed. Test files are written with the editor, never a heredoc. A swallowed exception in a guard of 15 lines or more calls `_degrade.degraded`.
- Each service suite runs on its own, from the repo root, with the main checkout's venv: `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest <path> -q -rf`. Compare the failing SET against main's before merging.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The feed ships with `show_in_dropdown = false`. The page's list changes only when the operator decides (spec 6).

## Review Focus

The inputs the spec implies and no feature test would otherwise reach, most likely first. Each has its test in the named task.

1. **ThinkorSwim connected but its values frozen.** Excel keeps answering with the last numbers, so the capture time advances and the page reads as current. Several identical captures in a row must mark the feed stale with a reason (Task 4).
2. **A half-filled capture right after a rebuild.** RTD fills over seconds; a snapshot of 30 of 700 contracts has plausible but wrong walls. It must be refused against the instrument's previous count, not published (Task 4).
3. **A capture arriving after its session ended, or out of order.** A 14:59 capture landing at 15:01 belongs to its own expiry, and an older capture must never replace a newer one in the cache (Task 4).
4. **Overnight quotes missing at the money.** No readable call and put pair is not a disagreement: the parity check records that it could not run and the snapshot proceeds, while a real disagreement refuses (Task 3).
5. **Writing the first Sunday-evening rows.** A new calendar date in `gex_history.db` must not cost `$SPX` and the rest one kept session, and the archive must not record a date it holds nothing for (Task 2).

---

### Task 1: Config and the shared vocabulary

**Files:**
- Create: `config/futures_chain.toml`, `shared/futures_chain_config.py`, `shared/futures_chain.py`
- Modify: `repo_paths.py` (`FUTURES_CHAIN_TOML`, beside `GAMMA_HEAT_TOML` at line 106), `shared/service_limits.py:59,78`, `config/services.toml:77`, `webgui/config_schema.py` (a `_FUTURES_CHAIN` `ConfigFile` in the list at line 2898), `CLAUDE.md` (Tier-1 allow-list, config file list)
- Test: `shared/tests/test_futures_chain.py`, `shared/tests/test_futures_chain_config.py`

**Interfaces:**
- Produces, in `shared/futures_chain_config.py` (same pattern as `shared/gamma_heat_config.py`): `instruments() -> dict[str, dict]` keyed `"ES"` (multiplier 50, `strike_step` 5, `parity_limit_pts` 15) and `"NQ"` (multiplier 20, `strike_step` 10, `parity_limit_pts` 60), both with `strike_width_pct` 5, the range measured, each `{"symbol", "label", "multiplier", "exchange", "strike_step", "strike_width_pct", "parity_limit_pts", "weekday_roots", "friday_root", "eom_root", "quarterly_root"}`; `capture() -> dict` with `interval_sec` (300), `max_age_sec` (900), `stale_after_sec` (660), `max_future_skew_sec` (30), `min_fill_fraction` (0.8), `frozen_repeats` (3), `fill_step_sec` (60), `frozen_before_expiry_min` (60), `alert_repeat_min` (240), `max_snapshot_kb` (512), `show_in_dropdown` (false); `session() -> dict` with `open_ct` ("17:00"), `close_ct` ("15:00").
- Produces, in `shared/futures_chain.py` (stdlib + the config module + `shared.symbols` only):
  - `STREAM = "cmd:futures_chain"`, `chain_key(symbol) -> str` (`cache:options:futures_chain:<symbol>`), `frozen_key(symbol) -> str` (`...:frozen`), `STATUS_KEY = "cache:options:futures_chain_status"`
  - `is_feed_symbol(symbol) -> bool`, `display_symbol(symbol) -> str` (unchanged for anything else), `feed_symbols() -> list[str]`, `instrument_for(symbol) -> str | None`
  - `session_expiry(now: datetime) -> date`, `in_session(now: datetime) -> bool`, `session_bounds(expiry: date) -> tuple[int, int]`
  - `candidate_series(instrument: str, expiry: date) -> list[str]`
  - `ContractTerms` (NamedTuple: `underlying: str, multiplier: int, expiry: date, am_settled: bool, series: str`), `parse_description(text) -> ContractTerms | None`
  - `fill_rows(rows: list, interval_sec: int, step_sec: int) -> list`
  - `date_text(expiry: date) -> str` (`"2 NOV 26"`), `build_plan(now: datetime) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_description_reads_the_contracts_own_terms():
    t = parse_description("/ESZ26:XCME 1/50 2 NOV 26 (Monday) (Wk1) /E1AX26:XCME 7600 CALL")
    assert t == ContractTerms("/ESZ26:XCME", 50, date(2026, 11, 2), False, "E1AX26")

def test_parse_description_marks_morning_settlement():
    t = parse_description("/ESZ26:XCME 1/50 18 DEC 26 (AM Settled) /ESZ26:XCME 7600 CALL")
    assert t.am_settled is True and t.expiry == date(2026, 12, 18)

def test_parse_description_reads_month_end_and_nq():
    assert parse_description("/ESZ26:XCME 1/50 30 NOV 26 (EOM) /EWX26:XCME 7600 CALL").series == "EWX26"
    assert parse_description("/NQZ26:XCME 1/20 2 NOV 26 (Monday) (Wk1) /Q1AX26:XCME 28500 CALL").multiplier == 20

@pytest.mark.parametrize("junk", ["N/A", "", None, 7600, "/ESZ26:XCME 1/50 99 NOV 26 /E1AX26:XCME 7600 CALL"])
def test_parse_description_refuses_what_it_cannot_read(junk):
    assert parse_description(junk) is None

def test_candidates_for_a_monday_include_the_month_end_name():
    names = candidate_series("ES", date(2026, 11, 30))
    assert "EWX26" in names and "E5AX26" in names

def test_candidates_for_a_friday():
    names = candidate_series("NQ", date(2026, 11, 20))
    assert "QN3X26" in names and "QNEX26" in names and "NQX26" in names

def test_a_sunday_evening_capture_belongs_to_monday():
    assert session_expiry(ct(2026, 11, 1, 18, 0)) == date(2026, 11, 2)

def test_after_the_close_the_next_session_is_named():
    assert session_expiry(ct(2026, 11, 2, 15, 30)) == date(2026, 11, 3)

def test_friday_after_the_close_rolls_past_the_weekend():
    assert session_expiry(ct(2026, 11, 6, 15, 30)) == date(2026, 11, 9)

@pytest.mark.parametrize("when,expected", [
    ((2026, 11, 2, 14, 59), True),   # Monday, before expiry
    ((2026, 11, 2, 15, 0), False),   # the daily gap
    ((2026, 11, 2, 17, 0), True),    # opens for Tuesday
    ((2026, 11, 6, 18, 0), False),   # Friday evening
    ((2026, 11, 7, 12, 0), False),   # Saturday
    ((2026, 11, 8, 16, 59), False),  # Sunday, before the open
    ((2026, 11, 8, 17, 0), True),
])
def test_in_session(when, expected):
    assert in_session(ct(*when)) is expected

def test_session_bounds_span_two_calendar_dates():
    lo, hi = session_bounds(date(2026, 11, 3))
    assert lo == ct(2026, 11, 2, 17, 0).timestamp() and hi == ct(2026, 11, 3, 15, 0).timestamp()

def test_fill_repeats_a_row_up_to_one_interval_and_leaves_a_longer_outage_a_gap():
    rows = [(0, "a"), (300, "b"), (1500, "c")]
    out = fill_rows(rows, interval_sec=300, step_sec=60)
    assert [r[0] for r in out[:5]] == [0, 60, 120, 180, 240]
    assert [r[1] for r in out[:5]] == ["a"] * 5
    assert 600 not in [r[0] for r in out]      # the outage after "b" stays empty
    assert out[-1] == (1500, "c")              # the newest row is not drawn into the future

def test_fill_keeps_the_rest_of_each_row_and_does_not_mutate_input():
    rows = [(0, 1, 2, 3), (300, 4, 5, 6)]
    assert fill_rows(rows, 300, 60)[1] == (60, 1, 2, 3) and len(rows) == 2

def test_plan_names_the_session_the_series_and_the_date_text():
    p = build_plan(ct(2026, 11, 1, 18, 0))
    assert p["expiry"] == "2026-11-02" and p["date_text"] == "2 NOV 26"
    assert p["interval_sec"] == 300 and p["in_session"] is True
    assert "E1AX26" in p["instruments"]["ES"]["candidates"]
    assert p["instruments"]["NQ"]["strike_step"] > 0
    assert p["valid_until"] == ct(2026, 11, 2, 15, 0).timestamp()

def test_is_feed_symbol_is_only_the_feed():
    assert is_feed_symbol("FOP.ES") and not is_feed_symbol("ES") and not is_feed_symbol("$SPX")

def test_feed_symbols_pass_the_ticker_rule_unchanged():
    assert all(clean_symbol(s) == s for s in feed_symbols())

def test_display_symbol():
    assert display_symbol("FOP.ES") == "/ES" and display_symbol("SPY") == "SPY"

def test_module_import_set_is_pinned():   # same AST shape as test_public_gamma.py
    assert imports_of(futures_chain) <= {"datetime", "re", "typing", "zoneinfo",
        "shared.futures_chain_config", "shared.symbols"}

def test_stream_cap_is_set_before_anything_writes_it():
    assert service_limits.stream_keep("cmd:futures_chain") == 20
```

`ct(...)` builds an `America/Chicago` aware datetime. The config test follows `test_gamma_heat_config.py`, including a read through `config/local`-free defaults, and the proof a value is READ by monkeypatching the accessor.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest shared/tests/test_futures_chain.py shared/tests/test_futures_chain_config.py -q`
Expected: FAIL, modules not found.

- [ ] **Step 3: Implement**

Series names come from `config` roots plus month code (`F G H J K M N Q U V X Z`) and two-digit year. `candidate_series` for a Monday to Thursday returns `{w}` weeks 1 to 5 of that weekday's root (`E1A`..`E5A`, `E1B`..), for a Friday weeks 1 to 5 of `EW{w}`, and always the month-end and quarterly names. Extra names are harmless because the PC and server keep only those whose description states this date. Quarterly month codes follow the expiry's own month and the next quarterly month.

`in_session` and `session_expiry` treat weekdays as the only expiries; holidays are not modelled, since CME's differ from the NYSE's and a series that does not resolve is reported instead. `session_bounds` starts at `open_ct` on the calendar day before the expiry.

Set `"cmd:futures_chain": 20` in `shared/service_limits.DEFAULTS["stream_keep"]`, `200` in `STREAM_KEEP_CEILINGS`, and in `config/services.toml`. Add the config catalogue entries. Add `shared.futures_chain` and `shared.futures_chain_config` to the Tier-1 allow-list in `CLAUDE.md`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest shared/tests/test_futures_chain.py shared/tests/test_futures_chain_config.py -q` and `python -m pytest webgui/tests/test_config_schema.py -q`, from `webgui` for the second.
Expected: PASS.

- [ ] **Step 5: Commit**

`feat(futures-chain): config and shared vocabulary for the /ES and /NQ feed`

---

### Task 2: Engine and store seams that leave existing symbols alone

**Files:**
- Modify: `options-scanner/gamma_tool.py:761,806-809`, `shared/raw_minute.py`, `options-scanner/gex_collector.py:820`, `options-scanner/gex_history_db.py:45,1155`, `options-scanner/gex_archive.py:99,149`
- Test: `options-scanner/tests/test_futures_chain_seams.py`

**Interfaces:**
- Produces: `GammaEngine.calc_all_from_chain(self, chain, use_volume=False, now=None, hours_left=None)`; `RawMinute.hours_left: dict = field(default_factory=dict)` keyed by symbol; `gex_history_db.SessionSpan` (NamedTuple `start_ts: int, end_ts: int`), accepted anywhere a `date` is by `_local_unix_range`.

- [ ] **Step 1: Write the failing tests**

```python
def test_hours_left_overrides_the_clock_formula():
    # 18:00 the evening before: the existing formula clamps to 0 hours.
    eng = GammaEngine()
    now = ct(2026, 11, 1, 18, 0)
    a = eng.calc_all_from_chain(chain_fixture(key="2026-11-02:0"), now=now, hours_left=21.0)
    b = eng.calc_all_from_chain(chain_fixture(key="2026-11-02:0"), now=ct(2026, 11, 2, 15, 0) - timedelta(hours=21), hours_left=21.0)
    assert a[1]["gex"] == b[1]["gex"]             # charm depends only on the time left given

def test_no_hours_left_is_byte_identical():
    eng = GammaEngine()
    now = ct(2026, 11, 2, 10, 0)
    assert eng.calc_all_from_chain(chain_fixture(), now=now) == eng.calc_all_from_chain(chain_fixture(), now=now, hours_left=None)

def test_derive_minute_passes_a_symbols_hours_left_and_only_that_symbols(monkeypatch):
    seen = spy_on_calc_all(monkeypatch)
    derive_minute(GammaEngine(), conn, RawMinute(ts=T, chains=[("FOP.ES", c), ("SPY", c)], hours_left={"FOP.ES": 21.0}))
    assert seen == {"FOP.ES": 21.0, "SPY": None}

def test_session_span_reads_across_two_calendar_dates():
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 2, 9, 0))
    span = SessionSpan(int(ct(2026, 11, 1, 17, 0).timestamp()), int(ct(2026, 11, 2, 15, 0).timestamp()))
    assert len(load_date_with_grid(conn, "FOP.ES", "gex", date=span)) == 2

@pytest.mark.parametrize("loader", [load_flow_series, load_hedge_series])
def test_the_other_session_loaders_take_a_span(loader):
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0))
    assert loader(conn, "FOP.ES", span_fixture())

def test_session_dte_takes_a_span():
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0), dte=0)
    assert session_dte(conn, "FOP.ES", "gex", span_fixture()) == 0

def test_a_sunday_of_feed_rows_costs_no_symbol_a_kept_session():
    for day in (26, 27, 28, 29, 30):
        insert(conn, "$SPX", "gex", ts=ct(2026, 10, day, 10, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0))
    purge_keep_sessions(conn, keep_sessions=5)
    assert count(conn, "$SPX") == 5 and count(conn, "FOP.ES") == 1

def test_the_archive_holds_no_feed_symbols_and_no_feed_only_date(tmp_path):
    insert(conn, "$SPX", "gex", ts=ct(2026, 10, 30, 10, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 10, 30, 18, 0))
    insert(conn, "FOP.ES", "gex", ts=ct(2026, 11, 1, 18, 0))
    assert closed_sessions(conn, ct(2026, 11, 2, 16, 0)) == ["2026-10-30"]
    assert archived_symbols_after_running_the_archive(conn, tmp_path) == {"$SPX"}
```

- [ ] **Step 2: Run to verify they fail**

Run, from `options-scanner`: `python -m pytest tests/test_futures_chain_seams.py -q`
Expected: FAIL (`hours_left` unexpected, `SessionSpan` missing).

- [ ] **Step 3: Implement**

`T` becomes `hours_left / (365 * 24)` floored at the same `1e-6` when `hours_left` is given; the existing formula is untouched otherwise. `_distinct_session_dates` gains `WHERE symbol NOT LIKE 'FOP.%'`; the purge's `ts < cutoff` delete still applies to every symbol. `gex_archive`'s key list carries the same exclusion. `gex_history_db` stays free of a `shared` import: the `FOP.%` pattern is a named constant there, pinned equal to `futures_chain`'s prefix by a test in `shared/tests/test_cross_tier_mirrors.py`.

If a span-taking loader does not go through `_local_unix_range`, route it through; do not add a second range path.

- [ ] **Step 4: Run to verify they pass, then the whole engine suite**

Run, from `options-scanner`: `python -m pytest tests -q -rf`
Expected: PASS, the same 2 skips as main.

- [ ] **Step 5: Commit**

`feat(gamma): optional time left and two-date sessions for the futures feed`

---

### Task 3: Snapshot model and the chain adapter

**Files:**
- Create: `shared/contracts/futures_chain.py`, `services/options_svc/futures_chain_adapter.py`
- Test: `shared/contracts/tests/test_futures_chain_contract.py`, `services/options_svc/tests/test_futures_chain_adapter.py`

**Interfaces:**
- Consumes: Task 1's `parse_description`, `instruments()`; `expiry_time.time_to_expiry_years(now_dt, expiry_date)`.
- Produces:
  - `FuturesContractRow` (`series: str, side: Literal["C","P"], strike: float, mark, bid, ask, volume, open_int, iv, delta: float | None`), which turns `"N/A"`, `""`, non-finite values and an unreadable percent into `None` and `"12.51%"` into `12.51`
  - `FuturesChainSnapshot` (`instrument: str, expiry: date, captured_at: float, descriptions: dict[str, str], underlying_symbol: str, underlying_price: float, contracts: list[FuturesContractRow]`)
  - `class Refused(Exception)` with `.reason: str`, and `build_chain(snapshot: FuturesChainSnapshot) -> AdaptedChain`
  - `AdaptedChain` (NamedTuple: `symbol: str, expiry: date, chain: dict, captured_at: float, hours_left: float, parity_gap: float | None, listed: int`)

- [ ] **Step 1: Write the failing tests**

```python
def test_row_turns_na_into_absent_and_a_percent_string_into_a_number():
    r = FuturesContractRow(series="E1AX26", side="C", strike=7600, mark="N/A", bid=None, ask="", volume=float("nan"), open_int=25, iv="12.51%", delta="N/A")
    assert (r.mark, r.bid, r.ask, r.volume, r.open_int, r.iv, r.delta) == (None, None, None, None, 25, 12.51, None)

def test_gamma_is_calculated_from_the_contracts_own_volatility():
    # F=7600, K=7600, sigma=0.15, 6.5 hours: phi(d1) / (F sigma sqrt(T))
    c = contract_at(adapt(snap(price=7600, rows=[row(7600, "C", iv="15%")]), hours=6.5), 7600, "C")
    assert c["gamma"] == pytest.approx(0.012847, rel=1e-3)   # T = 6.5/8760, by hand

@pytest.mark.parametrize("iv", ["N/A", "0%", "-1%", None])
def test_no_usable_volatility_is_no_gamma_not_zero(iv):
    c = contract_at(adapt(snap(rows=[row(7600, "C", iv=iv), row(7600, "P")])), 7600, "C")
    assert c["gamma"] is None or "gamma" not in c
    assert c["volatility"] == 0     # the engine's own charm and vanna skip

def test_no_usable_volatility_still_runs_through_the_engine():
    gex, *_ = GammaEngine().calc_all_from_chain(adapt(snap(rows=[row(7600, "C", iv="N/A", open_int=10), row(7600, "P", open_int=10)])).chain, now=ct(2026, 11, 2, 9, 0), hours_left=6.0)
    assert gex is not None

@pytest.mark.parametrize("inst,mult,scale", [("ES", 50, 0.5), ("NQ", 20, 0.2)])
def test_counts_are_scaled_by_contract_size(inst, mult, scale):
    c = contract_at(adapt(snap(instrument=inst, multiplier=mult, rows=[row(7600, "C", open_int=100, volume=40)])), 7600, "C")
    assert (c["openInterest"], c["totalVolume"]) == (100 * scale, 40 * scale)

def test_the_engines_dollar_figure_uses_the_true_multiplier():
    # gamma * OI * multiplier * F^2 * 0.01, with the engine's 100 cancelled by the scale
    adapted = adapt(snap(price=7600, rows=[row(7600, "C", iv="15%", open_int=100), row(7600, "P", iv="15%", open_int=0)]), hours=6.5)
    gex, *_ = GammaEngine().calc_all_from_chain(adapted.chain, now=ct(2026, 11, 2, 8, 30), hours_left=6.5)
    gamma = contract_at(adapted, 7600, "C")["gamma"]
    assert gex["gex"][7600.0]["call"] == pytest.approx(gamma * 100 * 50 * 7600 ** 2 * 0.01)

def test_two_series_on_one_expiry_add_at_a_strike():
    a = adapt(snap(descriptions=two_series(), rows=[row(7600, "C", series="E1AX26", open_int=10), row(7600, "C", series="EWX26", open_int=30), row(7600, "P", open_int=1)]))
    gex, *_ = GammaEngine().calc_all_from_chain(a.chain, now=ct(2026, 11, 2, 9, 0), hours_left=6.0)
    only_first = adapt(snap(rows=[row(7600, "C", series="E1AX26", open_int=40), row(7600, "P", open_int=1)]))
    gex2, *_ = GammaEngine().calc_all_from_chain(only_first.chain, now=ct(2026, 11, 2, 9, 0), hours_left=6.0)
    assert gex["gex"][7600.0]["call"] == pytest.approx(gex2["gex"][7600.0]["call"])

def test_chain_keeps_the_expiry_at_dte_zero_all_session():
    a = adapt(snap(expiry=date(2026, 11, 2), captured=ct(2026, 11, 1, 18, 0)))
    assert list(a.chain["callExpDateMap"]) == ["2026-11-02:0"]
    assert contract_at(a, 7600, "C")["daysToExpiration"] == 0
    assert a.hours_left == pytest.approx(21.0)

def test_the_real_flow_helpers_read_the_chain():
    a = adapt(full_ladder_fixture())
    assert flow_skew.index_call_put_premium(a.chain)["call_prem"] > 0
    assert iv_analysis.extract_atm_iv(a.chain) is not None

@pytest.mark.parametrize("field,value,reason", [
    ("description_date", "3 NOV 26", "expiry"),      # no series states this expiry
    ("description_settled", "(AM Settled)", "morning"),
    ("description_multiplier", "1/20", "multiplier"),
    ("underlying_symbol", "/ESH27:XCME", "underlying"),
    ("underlying_price", 0, "price"),
])
def test_refused_with_a_reason(field, value, reason):
    with pytest.raises(Refused, match=reason):
        adapt(bad_snapshot(field, value))

def test_a_series_that_fails_its_check_is_dropped_when_another_passes():
    a = adapt(snap(descriptions={**ok(), "EWX26": am_settled()}, rows=[row(7600, "C"), row(7600, "P"), row(7600, "C", series="EWX26")]))
    assert all(c["series"] != "EWX26" for c in contracts_of(a))

def test_parity_disagreement_refuses():
    with pytest.raises(Refused, match="parity"):
        adapt(snap(price=7600, rows=[row(7600, "C", mark=50), row(7600, "P", mark=10)]))    # implies 7640, limit below that

def test_parity_passes_when_within_the_limit_and_records_the_gap():
    assert adapt(snap(price=7600, rows=[row(7600, "C", mark=11), row(7600, "P", mark=10)])).parity_gap == pytest.approx(1.0)

def test_no_readable_pair_is_not_a_disagreement():
    assert adapt(snap(rows=[row(7600, "C", mark="N/A"), row(7600, "P", mark="N/A")])).parity_gap is None

def test_a_capture_past_expiry_is_refused():
    with pytest.raises(Refused, match="expiry"):
        adapt(snap(expiry=date(2026, 11, 2), captured=ct(2026, 11, 2, 15, 1)))

def test_adapter_imports_nothing_from_compute():
    assert not any(n.endswith("compute") for n in imports_of(futures_chain_adapter))
```

Fixtures use the parity pair at the strike nearest the price. A builder who computes a different gamma checks the formula before changing the number.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest services/options_svc/tests/test_futures_chain_adapter.py -q`, then `python -m pytest shared/contracts/tests/test_futures_chain_contract.py -q`.
Expected: FAIL.

- [ ] **Step 3: Implement**

The engine loops every contract in a strike's list, so a series gets its own entry and nothing is merged: `{"putCall", "strikePrice", "openInterest", "totalVolume", "volatility", "gamma", "delta", "mark", "bid", "ask", "daysToExpiration": 0, "series"}`. Check `_gamma()` and `_delta()` in `gamma_tool.py` for how they read `None` and adjust so an absent gamma contributes nothing rather than raising. Time left is `time_to_expiry_years(captured_dt, expiry)`, and `hours_left` is that times 8760, so one helper decides. Parity uses the strike nearest the price having both marks, searching outward to a few strikes; a limit breach refuses. Dropped series are logged.

- [ ] **Step 4: Run to verify they pass**

Same commands. Expected: PASS.

- [ ] **Step 5: Commit**

`feat(futures-chain): validate a captured chain and calculate its gamma`

---

### Task 4: Ingest CLI and the service consumer

**Files:**
- Create: `tools/futures_chain_cli.py`, `tools/futures_chain_remote.sh`, `services/options_svc/futures_chain_feed.py`
- Modify: `services/options_svc/app.py:53`
- Test: `tools/tests/test_futures_chain_cli.py`, `services/options_svc/tests/test_futures_chain_feed.py`

**Interfaces:**
- Consumes: Task 3's `build_chain`, `Refused`; Task 2's `RawMinute.hours_left`; `Bus.enqueue_command(stream, {"type", "args"})`.
- Produces:
  - CLI: `futures_chain_cli.py plan` prints `build_plan(now)` as JSON; `futures_chain_cli.py ingest` reads `{"snapshots": [...], "status": {...}}` from stdin (refused over `max_snapshot_kb` per snapshot) and enqueues one `{"type": "snapshot", "args": <snapshot>}` per instrument plus one `{"type": "pc_status", "args": ...}`; exit 0 on write.
  - `futures_chain_feed.handle(bus, command) -> None`, the extra consumer.
  - `futures_chain_feed.latest(bus, symbol) -> dict | None`, returning `{"chain", "captured_at", "expiry", "hours_left"}` from the cache.
  - Status view at `STATUS_KEY`: `{"FOP.ES": {"captured_at", "expiry", "listed", "parity_gap", "reason", "repeats", "refused_at"}, "pc": {"reason", "at"}}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_good_snapshot_writes_all_views_at_the_capture_time(bus, db):
    handle(bus, cmd(snapshot(captured=T)))
    assert views_at(db, "FOP.ES", ts_floor_minute(T)) == {"gex", "charm", "dex", "vanna", "prem"}

def test_the_chain_is_cached_for_the_page(bus, db):
    handle(bus, cmd(snapshot(captured=T)))
    assert latest(bus, "FOP.ES")["captured_at"] == T

def test_an_invalid_payload_is_not_published_and_counts_a_degrade(bus, db, degrade_count):
    handle(bus, cmd({"instrument": "ES", "contracts": "rubbish"}))
    assert latest(bus, "FOP.ES") is None and degrade_count() == 1

def test_a_refusal_is_reported_with_its_reason_and_writes_nothing(bus, db):
    handle(bus, cmd(snapshot(price=0)))
    assert "price" in status(bus)["FOP.ES"]["reason"] and rows(db) == 0

def test_an_old_snapshot_is_dropped_on_age(bus, db):
    handle(bus, cmd(snapshot(captured=now() - 901)))
    assert rows(db) == 0 and latest(bus, "FOP.ES") is None

def test_a_snapshot_from_the_future_is_dropped(bus, db):
    handle(bus, cmd(snapshot(captured=now() + 31)))
    assert rows(db) == 0

def test_one_instrument_failing_does_not_touch_the_other(bus, db):
    handle(bus, cmd(snapshot(instrument="NQ", price=0)))
    handle(bus, cmd(snapshot(instrument="ES")))
    assert latest(bus, "FOP.ES") and latest(bus, "FOP.NQ") is None

# Review Focus 1
def test_identical_values_repeating_are_marked_as_not_moving(bus, db):
    for i in range(3):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    assert status(bus)["FOP.ES"]["repeats"] == 3
    assert "not changing" in status(bus)["FOP.ES"]["reason"]

def test_a_changed_value_clears_the_repeat(bus, db):
    for i in range(3):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    handle(bus, cmd(snapshot(captured=T + 900, price=7601)))
    assert status(bus)["FOP.ES"]["repeats"] == 0 and not status(bus)["FOP.ES"]["reason"]

# Review Focus 2
def test_a_half_filled_capture_is_refused_against_the_last_count(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=30)))
    assert latest(bus, "FOP.ES")["captured_at"] == T
    assert "contracts" in status(bus)["FOP.ES"]["reason"]

def test_a_new_session_is_not_compared_with_the_last_sessions_count(bus, db):
    handle(bus, cmd(snapshot(captured=T, expiry=MON, n=300)))
    handle(bus, cmd(snapshot(captured=T + 8000, expiry=TUE, n=100)))
    assert latest(bus, "FOP.ES")["expiry"] == TUE.isoformat()

# Review Focus 3
def test_an_older_capture_never_replaces_a_newer_one_in_the_cache(bus, db):
    handle(bus, cmd(snapshot(captured=T + 300)))
    handle(bus, cmd(snapshot(captured=T)))
    assert latest(bus, "FOP.ES")["captured_at"] == T + 300

def test_a_late_arrival_keeps_its_own_expiry(bus, db):
    with frozen_clock(ct(2026, 11, 2, 15, 1)):
        handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 14, 59).timestamp(), expiry=date(2026, 11, 2))))
    assert latest(bus, "FOP.ES")["expiry"] == "2026-11-02"

def test_the_frozen_key_keeps_the_last_capture_before_the_cutoff(bus, db):
    handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 13, 55).timestamp())))
    handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 14, 30).timestamp())))
    assert frozen(bus, "FOP.ES")["captured_at"] == ct(2026, 11, 2, 13, 55).timestamp()

def test_a_pc_status_lands_in_the_status_view(bus):
    handle(bus, cmd_type("pc_status", {"reason": "ThinkorSwim logged out", "at": T}))
    assert status(bus)["pc"]["reason"] == "ThinkorSwim logged out"

def test_no_schwab_or_proxy_call_is_made(bus, db, network_guard):
    handle(bus, cmd(snapshot(captured=T)))
    assert network_guard.calls == []
```

CLI tests, with `main(argv, stdin=..., stdout=...)` and the fake bus:

```python
def test_plan_prints_json_a_powershell_can_read(capsys, frozen_sunday_evening):
    assert main(["plan"]) == 0
    assert json.loads(capsys.readouterr().out)["expiry"] == "2026-11-02"

def test_ingest_enqueues_one_command_per_instrument(fake_bus):
    assert main(["ingest"], stdin=io.StringIO(json.dumps({"snapshots": [es(), nq()], "status": {"reason": ""}}))) == 0
    assert stream_types(fake_bus, "cmd:futures_chain") == ["snapshot", "snapshot", "pc_status"]

def test_ingest_refuses_an_oversized_snapshot(fake_bus):
    assert main(["ingest"], stdin=huge()) != 0 and stream_types(fake_bus, "cmd:futures_chain") == []

def test_ingest_tolerates_a_bom_and_crlf_from_powershell(fake_bus):
    assert main(["ingest"], stdin=io.StringIO("\ufeff" + json.dumps({"snapshots": [es()]}) + "\r\n")) == 0

def test_remote_script_is_lf_and_loads_the_stack_env():
    raw = pathlib.Path("tools/futures_chain_remote.sh").read_bytes()
    assert b"\r" not in raw and b". ./.env" in raw and b"set -a" in raw
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest services/options_svc/tests/test_futures_chain_feed.py -q`, and `python -m pytest tools/tests/test_futures_chain_cli.py -q`.
Expected: FAIL.

- [ ] **Step 3: Implement**

Registered in `app.py`'s `extra_consumers` as `(futures_chain.STREAM, futures_chain_feed.handle)`. `handle` dispatches on `command.type`; nothing else runs here, so the stream cannot reach a paper book. The age gate uses the snapshot's `captured_at` and doubles as the replay guard for a consumer group created at id 0.

Write through a NEW `GammaEngine()` per snapshot (never a shared one: `_last_dte` is instance state), `gh.connect()`, `derive_minute(eng, conn, RawMinute(ts=floor_minute(captured_at), chains=[(symbol, chain)], hours_left={symbol: hours_left}))`, commit, close. A failure in the write path calls `_degrade.degraded("options.futures_chain")`.

`futures_chain_remote.sh`: `cd` to the script's parent, `set -a; . ./.env; set +a`, `exec .venv/bin/python -X utf8 tools/futures_chain_cli.py "$@"`. Confirm `.gitattributes` keeps it LF.

The repeat check compares `underlying_price` and the sum of volume and open interest. Reasons are in whole words.

- [ ] **Step 4: Run to verify they pass**

Same commands. Expected: PASS.

- [ ] **Step 5: Commit**

`feat(futures-chain): ingest a captured chain into the store and cache`

---

### Task 5: Snapshots from the stored chain, and no path to Schwab

**Files:**
- Create: `services/options_svc/futures_chain_snapshot.py`
- Modify: `services/options_svc/compute.py:3857,4410,4442-4497`, `services/options_svc/handlers.py:1818,3130`, `services/options_svc/futures_chain_feed.py`, `services/options_svc/tests/test_compute_module_shape.py` only if lines moved out
- Test: `services/options_svc/tests/test_futures_chain_snapshot.py`

**Interfaces:**
- Consumes: Task 4's `latest`, `frozen_key`; Task 1's `session_bounds`, `fill_rows`; Task 2's `SessionSpan`.
- Produces:
  - `FeedSession` (NamedTuple: `span: SessionSpan, priced_at: datetime, hours_left: float, fill: Callable[[list], list]`)
  - `compute.gamma_snapshot(symbol, chain=None, with_term=True, session=None)`, unchanged when `session` is `None`
  - `futures_chain_snapshot.snapshot(bus, symbol, now=None) -> dict | None`, adding `"feed": {"label", "captured_at", "expiry", "frozen": bool, "stale": bool, "reason", "strike_low", "strike_high"}` to the payload
  - `compute.gamma_symbol_options()` unchanged; `futures_chain_snapshot.private_symbol_options(options) -> list`

- [ ] **Step 1: Write the failing tests**

```python
def test_snapshot_loads_rows_from_both_calendar_dates(bus, db):
    write_captures(db, "FOP.ES", ct(2026, 11, 1, 18, 0), ct(2026, 11, 2, 9, 0))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))
    ts = {r[0] for r in snap["views"]["GEX"]["history"]}
    assert int(ct(2026, 11, 1, 18, 0).timestamp()) in ts and int(ct(2026, 11, 2, 9, 0).timestamp()) in ts

def test_rows_are_filled_to_the_step_and_a_gap_stays(bus, db):
    write_captures(db, "FOP.ES", ct(2026, 11, 2, 9, 0), ct(2026, 11, 2, 9, 5), ct(2026, 11, 2, 10, 0))
    ts = [r[0] for r in snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 10, 1))["views"]["GEX"]["history"]]
    assert int(ct(2026, 11, 2, 9, 1).timestamp()) in ts
    assert int(ct(2026, 11, 2, 9, 30).timestamp()) not in ts

def test_a_feed_snapshot_does_not_disturb_another_symbols_memo(bus, db):
    primed = compute._history_rows_incremental(gh, db, "$SPX", "gex", date(2026, 11, 2))
    memo_date = compute._HIST_MEMO["date"]
    snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))
    assert compute._HIST_MEMO["date"] == memo_date and ("$SPX", "gex") in compute._HIST_MEMO["data"]

def test_priced_at_the_capture_not_the_wall_clock(bus, db, spy_calc_all):
    write_captures(db, "FOP.ES", ct(2026, 11, 1, 18, 0))
    snapshot(bus, "FOP.ES", now=ct(2026, 11, 1, 18, 4))
    assert spy_calc_all.now == ct(2026, 11, 1, 18, 0) and spy_calc_all.hours_left == pytest.approx(21.0)

def test_no_term_for_the_feed(bus, db):
    assert snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))["term"] == {}

def test_no_projected_band_before_the_expiry_days_stock_open(bus, db):
    assert "forward" not in snapshot(bus, "FOP.ES", now=ct(2026, 11, 1, 18, 4))["views"]["GEX"]

def test_stale_after_the_limit_with_the_numbers_kept_and_marked(bus, db):
    write_captures(db, "FOP.ES", ct(2026, 11, 2, 9, 0))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 20))
    assert snap["feed"]["stale"] is True and snap["views"]["GEX"]["data"]

def test_between_sessions_shows_the_frozen_capture_labelled_with_its_time(bus, db):
    write_captures(db, "FOP.ES", ct(2026, 11, 6, 13, 55), ct(2026, 11, 6, 14, 55))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 7, 12, 0))
    assert snap["feed"]["frozen"] is True
    assert snap["feed"]["captured_at"] == ct(2026, 11, 6, 13, 55).timestamp()
    assert snap["views"]["GEX"]["history"]        # the heatmap keeps the session

def test_the_feed_block_states_the_captured_strike_range(bus, db):
    write_captures(db, "FOP.ES", ct(2026, 11, 2, 9, 0))
    feed = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))["feed"]
    assert feed["strike_low"] < feed["strike_high"]

def test_no_capture_ever_is_the_empty_view(bus, db, proxy_spy):
    handlers.refresh_gamma(bus, "FOP.NQ")
    snap = bus.cache_get(handlers.CACHE_GAMMA).payload
    assert snap["views"] == {} and snap["symbol"] == "FOP.NQ" and proxy_spy.calls == []

def test_a_capture_repaints_only_when_the_page_is_on_that_symbol(bus, db, spy_refresh):
    set_current_gamma_symbol(bus, "$SPX")
    futures_chain_feed.handle(bus, cmd(snapshot(captured=T)))
    assert spy_refresh.calls == []
    set_current_gamma_symbol(bus, "FOP.ES")
    futures_chain_feed.handle(bus, cmd(snapshot(captured=T + 300)))
    assert spy_refresh.calls == ["FOP.ES"]

@pytest.mark.parametrize("fn", [lambda: compute._gamma_fetch_chain("FOP.ES"), lambda: compute.gamma_snapshot("FOP.ES")])
def test_the_chain_fetch_refuses_feed_symbols_without_a_call(fn, proxy_spy):
    assert fn() is None and proxy_spy.calls == []

def test_refresh_gamma_for_the_feed_makes_no_schwab_call(bus, db, proxy_spy):
    write_captures(db, "FOP.ES", ct(2026, 11, 2, 9, 0))
    handlers.refresh_gamma(bus, "FOP.ES")
    assert proxy_spy.calls == [] and bus.cache_get(handlers.CACHE_GAMMA).payload["symbol"] == "FOP.ES"

def test_existing_symbol_snapshot_is_unchanged_by_the_new_parameter(chain, frozen_market):
    assert compute.gamma_snapshot("SPY", chain=chain) == compute.gamma_snapshot("SPY", chain=chain, session=None)

def test_the_feed_stays_out_of_the_collection(monkeypatch_show_true):
    assert not any(is_feed_symbol(s) for s in gex_collector.collection_symbols())

def test_the_private_list_gets_the_feed_only_when_shown(bus, monkeypatch_show_true):
    handlers.publish_gamma_symbols(bus)
    assert "FOP.ES" in bus.cache_get(handlers.CACHE_GAMMA_SYMBOLS).payload["symbols"]
    assert not any(is_feed_symbol(s) for s in bus.cache_get(public_gamma.SYMBOLS_KEY).payload["symbols"])

def test_the_private_list_is_unchanged_while_not_shown(bus):
    handlers.publish_gamma_symbols(bus)
    assert not any(is_feed_symbol(s) for s in bus.cache_get(handlers.CACHE_GAMMA_SYMBOLS).payload["symbols"])

def test_a_feed_snapshot_never_gets_a_public_target():
    assert handlers._gamma_pub_targets("FOP.ES", hot=()) == ()

def test_the_explain_path_answers_without_a_chain(proxy_spy):
    compute.gamma_explain("FOP.ES")
    assert proxy_spy.calls == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest services/options_svc/tests/test_futures_chain_snapshot.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

The `compute.py` edits, 28 lines of headroom, nothing more than:
- `_gamma_fetch_chain`: return `None` first thing for a feed symbol. This covers its other call sites at 5493, 6994, 7497 and 9454.
- `_history_rows_incremental`: a `SessionSpan` goes straight to `gh.load_date_with_grid` and never touches `_HIST_MEMO`, which holds a single date for every symbol.
- `gamma_snapshot`: `now = session.priced_at if session else _sched._market_now()`; `session_date = session.span if session else ...`; `rth = None if session else ...`, which `_rth_only` already treats as a passthrough; `calc_all_from_chain(chain, now=..., hours_left=...)` when `session`; `_history` applies `session.fill`; `hedge_history` and the `prem` ladder take the same span. `_forward.attach` stays and runs for a session only inside the expiry day's stock window; assert what `forward_marks` returns at 18:00 and gate on it.

`private_symbol_options` appends `feed_symbols()` when `show_in_dropdown` is on. `publish_gamma_symbols` writes that list to the private key and the unchanged one to the public key. `refresh_gamma` routes a feed symbol to `futures_chain_snapshot.snapshot(bus, symbol)`, imported lazily there, with the same empty payload when it returns `None`. `futures_chain_feed.handle` calls `refresh_gamma` after its commit when `handlers._current_gamma_symbol(bus)` is this symbol. Strike low and high are read off the stored chain.

Between sessions: use the frozen key's chain with the priced-at time of that capture and the span of its expiry. If `gamma_explain`, `gamma_analyze` or `_term_chain` need a guard beyond the fetch refusal, add it in the handler, not in `compute`.

Measure the filled payload here: `len(json.dumps(snap))` for a 22-hour session against a regular `$SPX` session, and put the number in the commit message. If the filled history is over about twice `$SPX`'s, set `fill_step_sec` to 120 or 300 and record why.

- [ ] **Step 4: Run to verify they pass, then the whole service suite**

Run: `python -m pytest services/options_svc -q -rf`
Expected: PASS, including `test_compute_does_not_grow`.

- [ ] **Step 5: Commit**

`feat(gamma): build the /ES and /NQ snapshot from the stored chain, never Schwab`

---

### Task 6: Staleness alert and `/health`

**Files:**
- Create: `services/options_svc/futures_chain_watch.py`
- Modify: `services/options_svc/scheduler.py` (a branch in `loop`), `services/_scaffold.py:470,576` (optional `health_extra`), `services/options_svc/app.py`
- Test: `services/options_svc/tests/test_futures_chain_watch.py`, `services/tests/test_scaffold.py` (existing)

**Interfaces:**
- Consumes: Task 1's `in_session`; `shared.notify.system_alert.send(title, lines)`; Task 4's status view.
- Produces: `decide(now, status, state) -> tuple[list[Alert], dict]` (pure); `tick(bus, now=None) -> None`; `health(bus) -> dict`; `make_app(..., health_extra=None)` merging a dict under `/health`'s body, never touching `up`.

- [ ] **Step 1: Write the failing tests**

```python
def test_alerts_once_when_capture_stops_in_session():
    alerts, state = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0)), {})
    assert [a.kind for a in alerts] == ["stopped"]
    assert decide(ct(2026, 11, 2, 9, 25), status(captured=ct(2026, 11, 2, 9, 0)), state)[0] == []

def test_repeats_only_after_the_configured_time():
    _, state = decide(ct(2026, 11, 2, 0, 0), status(captured=ct(2026, 11, 1, 23, 0)), {})
    assert decide(ct(2026, 11, 2, 3, 59), status(captured=ct(2026, 11, 1, 23, 0)), state)[0] == []
    assert len(decide(ct(2026, 11, 2, 4, 1), status(captured=ct(2026, 11, 1, 23, 0)), state)[0]) == 1

@pytest.mark.parametrize("quiet", [(2026, 11, 2, 16, 0), (2026, 11, 7, 12, 0), (2026, 11, 8, 16, 30)])
def test_quiet_in_the_daily_gap_and_the_weekend(quiet):
    assert decide(ct(*quiet), status(captured=ct(2026, 11, 2, 9, 0)), {})[0] == []

def test_the_first_minutes_of_a_session_do_not_alert():
    assert decide(ct(2026, 11, 8, 17, 5), status(captured=ct(2026, 11, 6, 14, 55)), {})[0] == []

def test_clears_with_one_message_when_it_resumes():
    _, state = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0)), {})
    alerts, state = decide(ct(2026, 11, 2, 9, 30), status(captured=ct(2026, 11, 2, 9, 29)), state)
    assert [a.kind for a in alerts] == ["resumed"]
    assert decide(ct(2026, 11, 2, 9, 35), status(captured=ct(2026, 11, 2, 9, 34)), state)[0] == []

def test_values_not_changing_alerts_too():
    assert [a.kind for a in decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 19), repeats=3), {})[0]] == ["stopped"]

def test_the_pcs_reason_goes_in_the_message():
    alerts, _ = decide(ct(2026, 11, 2, 9, 20), status(captured=ct(2026, 11, 2, 9, 0), pc_reason="ThinkorSwim logged out"), {})
    assert "logged out" in " ".join(alerts[0].lines)

def test_one_instrument_stopping_names_that_instrument():
    alerts, _ = decide(ct(2026, 11, 2, 9, 20), both(es=ct(2026, 11, 2, 9, 19), nq=ct(2026, 11, 2, 9, 0)), {})
    assert "/NQ" in alerts[0].title and "/ES" not in alerts[0].title

def test_tick_survives_a_failed_send_and_keeps_its_state(bus, monkeypatch):
    monkeypatch.setattr(system_alert, "send", raises)
    tick(bus, now=ct(2026, 11, 2, 9, 20))     # no exception

def test_alert_state_lives_in_redis_so_a_restart_does_not_re_alert(bus, spy_send):
    write_status(bus, captured=ct(2026, 11, 2, 9, 0))
    tick(bus, now=ct(2026, 11, 2, 9, 20))
    importlib.reload(futures_chain_watch)       # a restart: no state in memory
    futures_chain_watch.tick(bus, now=ct(2026, 11, 2, 9, 21))
    assert len(spy_send.calls) == 1

def test_health_reports_the_feed_and_never_the_verdict(client):
    body = client.get("/health").json()
    assert "futures_chain" in body and body["up"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest services/options_svc/tests/test_futures_chain_watch.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

A scheduler branch `("futures_chain_watch", ...)`, launched each tick through `launch_branches` and guarded like `_periodic_branch`. Alert state goes in a small Redis key so a restart does not re-alert. A session's first minutes are quiet for `stale_after_sec` after its open. A service with the feed turned off has `health_extra=None` and an unchanged `/health`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest services/options_svc/tests/test_futures_chain_watch.py -q` and `python -m pytest services/tests -q -rf`
Expected: PASS.

- [ ] **Step 5: Commit**

`feat(futures-chain): push an alert when capture stops in session, and report the feed on health`

---

### Task 7: The capture on the PC

**Files:**
- Create: `tools/tos_capture/tos_capture.ps1`, `tools/tos_capture/install_tos_capture.ps1`
- Test: `tools/tests/test_tos_capture_script.py`

**Interfaces:**
- Consumes: Task 4's `tools/futures_chain_remote.sh plan` and `ingest` (`ssh -o BatchMode=yes -o ConnectTimeout=20 vps2 /home/administrator/dev/tools/futures_chain_remote.sh <verb>`; no inner quotes, which PowerShell 5.1 drops).
- Produces: `tos_capture.ps1 [-Once] [-NoSend] [-OutFile <path>] [-PlanFile <path>]`; a log at `%LOCALAPPDATA%\TosCapture\capture.log`; `%USERPROFILE%\Documents\ToS 0DTE\<expiry>.xlsx`, a sheet per instrument; a task named `ToS 0DTE Capture`, at logon, one instance, restart on failure, run only when the user is logged on.

The cycle, in order:
1. Start Excel, tracking its process id; a failed start is logged and retried. Fetch the plan at start and whenever `valid_until` passes. On failure, log and retry.
2. If no `ThinkorSwim` process exists, or the probe cells all read `N/A`, send `pc_status` with the reason, sleep to the next boundary, and repeat. Never start ThinkorSwim, never enter anything.
3. For each instrument, read `DESCRIPTION` of each candidate at a few round strikes around the last price; keep those containing the plan's `date_text` and not `(AM Settled)`. The underlying future is the one the description names.
4. Read the future's `MARK`, then write the ladder's formulas (`DESCRIPTION` once per series, then `MARK BID ASK VOLUME OPEN_INT IMPL_VOL DELTA` per contract, centred on the price, `strike_width_pct` wide, every `strike_step`) as one bulk `$rng.Formula = object[,]`.
5. On each clock boundary of `interval_sec`: read `Value2`, keep rows that are not `N/A` on every field, send one JSON document over one `ssh` by `System.Diagnostics.Process` with redirected stdin, and append the values to the dated workbook.
6. When the plan's expiry changes, clear and rewrite the formulas without restarting Excel if possible.

- [ ] **Step 1: Write the failing test**

The suite runs on Linux CI with no PowerShell, so this pins the rules at source level, as `test_proxy.py` does:

```python
SRC = pathlib.Path("tools/tos_capture/tos_capture.ps1").read_text(encoding="utf-8")

def test_starts_its_own_excel_and_stops_it_by_process_id():
    assert "/automation -Embedding" in SRC and re.search(r"Stop-Process\s+-Id\s+\$", SRC)

def test_never_stops_excel_by_name():
    assert not re.search(r"Stop-Process\s+-Name|taskkill", SRC, re.I)

def test_never_enters_anything_or_starts_thinkorswim():
    for banned in ("SendKeys", "password", "Start-Process \"thinkorswim", "PlaceOrder"):
        assert banned.lower() not in SRC.lower()

def test_ssh_is_unattended_with_no_inner_quotes():
    assert "BatchMode=yes" in SRC and "ConnectTimeout" in SRC
    assert "futures_chain_remote.sh" in SRC

def test_reads_the_terms_off_the_contract():
    assert "DESCRIPTION" in SRC and "AM Settled" in SRC

def test_has_no_hard_coded_interval_or_series():
    assert not re.search(r"\b300\b", SRC) and "E1A" not in SRC and "interval_sec" in SRC

def test_ascii_only_for_powershell_5():
    assert SRC.isascii()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tools/tests/test_tos_capture_script.py -q`
Expected: FAIL, file not found.

- [ ] **Step 3: Implement both scripts**

`install_tos_capture.ps1` copies the capture to `%LOCALAPPDATA%\TosCapture\`, so a branch change in a checkout cannot alter a running feed, and registers the task hidden. The operator runs it; it is not run during the build.

- [ ] **Step 4: Run to verify the test passes, then run the script once by hand**

Run: `python -m pytest tools/tests/test_tos_capture_script.py -q`
Expected: PASS.

Run on the PC, with ThinkorSwim open: `powershell -File tools/tos_capture/tos_capture.ps1 -Once -NoSend -OutFile "$env:TEMP\tos_once.json"`
Expected: the JSON holds both instruments, descriptions stating the plan's date, and at least 200 contracts; the log shows the Excel process id started and stopped; afterwards `Get-CimInstance Win32_Process -Filter "Name='EXCEL.EXE'"` lists no `/automation` copy. Feed that file through the Python adapter in a scratch check and confirm a `gex` result. With no server plan available yet, use `-PlanFile` for a saved `build_plan` output.

- [ ] **Step 5: Commit**

`feat(futures-chain): capture /ES and /NQ from ThinkorSwim on the PC`

---

### Task 8: The page

**Files:**
- Create: `webgui/pages/options/gamma_feed.py`
- Modify: `webgui/pages/options/gamma.py:2328` (`symbol_options` caller at 3362), the tab sync, the time-axis staleness check near 1879-1911, `webgui/page_help.py`
- Test: `webgui/tests/test_gamma_feed.py`

**Interfaces:**
- Consumes: Task 1's `is_feed_symbol`, `display_symbol`; Task 5's `snap["feed"]`.
- Produces, in `gamma_feed.py`: `select_options(symbols: list[str]) -> list | dict` (a plain list when no feed symbol is present, so existing behaviour is untouched; else `{value: label}`); `hidden_views(symbol) -> frozenset` (`{"Flow", "Term"}` for a feed symbol, else empty); `shows_analysis(symbol) -> bool`; `feed_label(snap, now) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_options_stay_a_plain_list_with_no_feed_symbol():
    assert select_options(["$SPX", "SPY"]) == ["$SPX", "SPY"]

def test_feed_symbols_show_their_label_and_keep_their_value():
    assert select_options(["$SPX", "FOP.ES"]) == {"$SPX": "$SPX", "FOP.ES": "/ES"}

def test_flow_and_term_are_hidden_for_the_feed_only():
    assert hidden_views("FOP.NQ") == {"Flow", "Term"} and hidden_views("SPY") == frozenset()

def test_analysis_is_not_offered_for_the_feed():
    assert shows_analysis("FOP.ES") is False and shows_analysis("$SPX") is True

def test_stale_label_states_the_age():
    assert feed_label(snap(stale=True, captured=NOW - 1260), NOW) == "Stale: captured 21 minutes ago"

def test_frozen_label_states_the_time():
    assert feed_label(snap(frozen=True, captured=ct(2026, 11, 6, 13, 55).timestamp()), NOW).startswith("Last session, as of ")

def test_current_label_states_the_capture_time_not_stale():
    assert "Stale" not in feed_label(snap(captured=NOW - 60), NOW)

def test_no_feed_block_no_label():
    assert feed_label({"symbol": "SPY"}, NOW) is None

def test_reason_is_carried_on_the_label():
    assert "not changing" in feed_label(snap(stale=True, reason="values not changing", captured=NOW - 60), NOW)

def test_heatmap_uses_the_whole_session_for_the_feed(fake_bus):
    # rows at 18:00 the evening before and 09:00: both columns appear, in order
    assert column_times(render_heatmap(feed_snapshot_with_rows())) == ["18:00", "09:00"]

def test_the_label_states_the_captured_range_when_it_was_narrowed():
    assert "7,400.00 to 7,800.00" in feed_label(snap(captured=NOW - 60, strike_low=7400, strike_high=7800), NOW)

def test_the_new_module_keeps_to_the_allow_list():
    assert imports_of(gamma_feed) <= {"nicegui", "shared.futures_chain", "datetime", "pages.fmt", "pages.ui_kit"}

def test_the_public_page_never_offers_a_feed_symbol(fake_bus):
    fake_bus.cache_set(public_gamma.SYMBOLS_KEY, {"symbols": ["$SPX"]})
    assert not any(is_feed_symbol(s) for s in public_dropdown(fake_bus))
```

- [ ] **Step 2: Run to verify they fail**

Run, from `webgui`: `python -m pytest tests/test_gamma_feed.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Page code goes in `gamma_feed.py`, keeping `gamma.render` under its ceiling. Search the page for every printed count (`contracts`, `call_vol`, `put_vol`, `totalVolume`, `openInterest`, `strike_count` is fine) and list them in the commit message; each is gone with the Flow tab or corrected. If there are more than a few, stop and reconsider a real engine parameter, as spec 5.4 asks. The `status_strip_text` countdown and the window check at 1879-1911 use the NYSE window: skip them for a feed symbol and show `feed_label` instead. A view switched to while hidden falls back to GEX, and no new switch overrides an existing one. Tailwind classes through the kit, with no inline style. In `page_help.py`, add what /ES and /NQ are, where they come from, and that they can go stale.

- [ ] **Step 4: Run to verify they pass, then check in a browser**

Run, from `webgui`: `python -m pytest tests -q -rf`
Expected: PASS.

Run `tools/ui_harness.py` on a free loopback port with a fake bus holding a built feed snapshot and `show_in_dropdown` on, and open it through `.claude/launch.json`. Check: the dropdown says /ES and /NQ; Flow and Term are gone; the heatmap draws on its axis with /NQ's uneven strikes (`uniform_strike_grid`, `raster_range`); contours and bars line up; a stale snapshot shows its label; the console has no errors. Take a screenshot. Stop the harness.

- [ ] **Step 5: Commit**

`feat(gamma): /ES and /NQ on Dealer Positioning, with their own stale label`

---

### Task 9: Documents

**Files:**
- Modify: `CLAUDE.md` (the service's new stream, the `FOP.*` rule, the two-date session, the archive exclusion, in a line or two each), `docs/reference/options-engine-invariants.md`, `docs/CHANGELOG.md`, `docs/webgui-routes.md`, `docs/manuals/` (User Guide: reading /ES and /NQ, stale label, the PC must be on; Technical Reference: gamma formula, contract-size scaling, cadence; API Reference: the stream and the snapshot's fields), `docs/manuals/diagrams/make_diagrams.py` if a process flow is drawn there

- [ ] **Step 1: Write the documents, correcting in place**

No test counts, no commit SHAs, no "verified live". `tests/test_claude_md_size.py` still passes.

- [ ] **Step 2: Run the guards and build the manuals**

Run: `python -m pytest tests/test_claude_md_size.py tests/test_ci_covers_every_suite.py -q`, then `python build_docs.py` from the folder that holds it.
Expected: PASS, and the manuals build.

- [ ] **Step 3: Commit**

`docs(futures-chain): the feed in the manuals, routes and reference`

---

### Task 10: Shadow session (needs the operator)

Not a code task. It starts only once the operator says so, because it stops the stack on promote and puts a long-running task on their PC.

- [ ] **Step 1: Merge and promote outside market hours**

Merge to `main`, push, and hand the operator the promote line. With `show_in_dropdown = false` the page does not change.

- [ ] **Step 2: Operator installs the capture**

Run on the PC: `powershell -File tools/tos_capture/install_tos_capture.ps1`, with ThinkorSwim open and logged in. First possible session: Sunday 2026-11-01 from 17:00 CT, for Monday's expiry.

- [ ] **Step 3: Read a whole overnight and regular session against ThinkorSwim**

Check on the server, through `Bus()` and `gex_history.db`: rows every interval across the night; `status` reasons empty; computed gamma against ThinkorSwim's 2-decimal value late in the session wherever it is large enough to compare; `parity_gap` overnight; the alert firing when the capture is stopped on purpose, and clearing. On the PC: capture seconds, snapshot size, Excel and ThinkorSwim memory, any lag while the operator trades. Confirm CME's open and expiry times and correct `config/futures_chain.toml` if they differ. Confirm what `QN1` and `QN2` do on Friday 2026-11-06.

- [ ] **Step 4: Report the measurements and compare the weightings over a few sessions**

Report open-interest against volume weighting. Change nothing without the operator.

- [ ] **Step 5: The operator decides whether to show them**

On their yes, set `show_in_dropdown = true` in `config/local/futures_chain.toml`, restart, and verify the page live.
