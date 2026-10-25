# Gamma Regime Study (phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the declared net-gamma regime study for the Opening Scalp's 15-minute
calls: the pure study module, the read-only report that refuses to show any result
before the 40th usable session, and the live-vs-backfill sign agreement check.

**Architecture:** One pure module, `services/options_svc/scalp_regime_study.py`, holds
the gap rule, the session count, the effect, the permutation and the pass rule, plus
two loaders that read from a connection they are handed. Two tools open `scalp.db`
read-only and call it: `tools/scalp_regime_report.py` prints Markdown, and
`tools/scalp_regime_agree.py` compares a `scalp_prob_backfill.py extract` file with
the live record and writes a small JSON result the report reads. The declared numbers
live in a new `[regime]` section of `config/scalp.toml`.

**Tech Stack:** Python 3.11, sqlite3, pytest. No numpy needed (pure-Python permutation
is fast enough at 40 days); the module may use `random.Random`.

**Spec:** [2026-10-25-scalp-gamma-regime-study-design.md](2026-10-25-scalp-gamma-regime-study-design.md) (approved 2026-10-25). Read §3 and §5 before starting.

## Global Constraints

- Declared values (config defaults, exactly): `start_day = "2026-10-21"`,
  `min_sessions = 40`, `alpha = 0.05`, `min_effect_pp = 5.0`,
  `order = ["afternoon", "midday", "morning"]`, `permutations = 10000`,
  `seed = 20261025`, `min_sign_agreement = 0.95`, `agree_min_sessions = 5`.
- Long roles decide: `long_with` and `long_against`. Labels through
  `scalp_study._label` (win 1, loss/timeout 0, skip none); multipliers through
  `scalp_study._scale_key`; the decision multiplier is `scalp_config.iv_scale()`, the
  every-multiplier check is `scalp_config.iv_scales()`.
- The regime comes from `intraday_readings` ONLY. `prob_sources` is never read.
- Blocks come from `scalp_config.intraday_block(slot)`; a slot with no block (the
  08:36 record) is never used. Days in `scalp_config.early_close_days()` are never used.
- Every store is opened read-only by the tools (`?mode=ro` URI, as
  `scalp_intraday_report._open_read_only` does). Never call `scalp_store.connect` on the
  real store from these tools: it creates the schema and switches on WAL.
- The service module imports nothing from `compute` and touches no bus.
- House rules: every new TOML key gets a `webgui/config_schema.py` Field; config read
  through `shared/scalp_config.py` accessors that fall back to defaults and never raise;
  tests written with the editor, never a heredoc; `git add <exact paths>` only (another
  session may share the tree).
- Venv (a worktree needs the absolute path): `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`, below `$PY`.

## Review Focus

1. **Running the report after session 40 gives the same answer.** The study uses
   exactly the FIRST `min_sessions` usable sessions; sessions 41+ never enter. (Task 3
   test `test_sessions_after_the_minimum_are_ignored`.)
2. **A backfilled `prob_sources` row for a test-window day changes nothing**, even with
   the opposite sign. (Task 4 test `test_report_ignores_prob_sources`.)
3. **The 08:36 morning record is never a reading**: it sits in `intraday_readings`
   under slot `"08:36"`, which has no block. (Task 2 test
   `test_usable_sessions_ignore_the_0836_record`.)
4. **`SPX` and `$SPX` are one symbol** in `snapshot.matrix`; the match goes through
   `scalp_confirm._norm`. (Task 2 test `test_regime_of_matches_symbol_without_dollar`.)
5. **A day with afternoon readings but no graded long outcome is not usable** (a
   direction-less tape writes readings but no outcomes). (Task 2 test
   `test_day_without_afternoon_outcomes_is_not_usable`.)

---

### Task 1: The `[regime]` configuration

**Files:**
- Modify: `shared/scalp_config.py` (`DEFAULTS` gains `"regime"`; accessors after the `prob_*` ones)
- Modify: `config/scalp.toml` (new `[regime]` section at the end)
- Modify: `webgui/config_schema.py` (a `Section("Gamma regime study", ...)` at the end of `_SCALP.sections`)
- Test: `shared/tests/test_scalp_config.py`

**Interfaces:**
- Produces (all in `shared.scalp_config`):
  `regime_start_day() -> datetime.date`, `regime_min_sessions() -> int`,
  `regime_alpha() -> float`, `regime_min_effect_pp() -> float`,
  `regime_order() -> list[str]`, `regime_permutations() -> int`,
  `regime_seed() -> int`, `regime_min_sign_agreement() -> float`,
  `regime_agree_min_sessions() -> int`.

- [ ] **Step 1: Write the failing tests**

```python
def test_regime_defaults():
    import datetime as dt
    from shared import scalp_config as sc
    assert sc.regime_start_day() == dt.date(2026, 10, 21)
    assert sc.regime_min_sessions() == 40
    assert sc.regime_alpha() == 0.05
    assert sc.regime_min_effect_pp() == 5.0
    assert sc.regime_order() == ["afternoon", "midday", "morning"]
    assert sc.regime_permutations() == 10000
    assert sc.regime_seed() == 20261025
    assert sc.regime_min_sign_agreement() == 0.95
    assert sc.regime_agree_min_sessions() == 5


def test_regime_bad_values_read_as_defaults(monkeypatch):
    from shared import scalp_config as sc
    bad = {"start_day": "21/10/2026", "min_sessions": 0, "alpha": 1.0,
           "min_effect_pp": -1, "order": ["afternoon", "afternoon", "morning"],
           "permutations": 50, "seed": -3, "min_sign_agreement": 0,
           "agree_min_sessions": 0}
    monkeypatch.setattr(sc, "load", lambda: {"regime": bad})
    test_regime_defaults()


def test_regime_good_values_are_read(monkeypatch):
    import datetime as dt
    from shared import scalp_config as sc
    good = {"start_day": "2026-11-02", "min_sessions": 60, "alpha": 0.01,
            "min_effect_pp": 0, "order": ["midday", "afternoon", "morning"],
            "permutations": 500, "seed": 7, "min_sign_agreement": 1.0,
            "agree_min_sessions": 3}
    monkeypatch.setattr(sc, "load", lambda: {"regime": good})
    assert sc.regime_start_day() == dt.date(2026, 11, 2)
    assert sc.regime_min_sessions() == 60
    assert sc.regime_alpha() == 0.01
    assert sc.regime_min_effect_pp() == 0.0
    assert sc.regime_order() == ["midday", "afternoon", "morning"]
    assert sc.regime_permutations() == 500
    assert sc.regime_seed() == 7
    assert sc.regime_min_sign_agreement() == 1.0
    assert sc.regime_agree_min_sessions() == 3
```

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest shared/tests/test_scalp_config.py -k regime -q`
Expected: FAIL, `AttributeError: module 'shared.scalp_config' has no attribute 'regime_start_day'`

- [ ] **Step 3: Implement the defaults and accessors**

Validation, one rule each (bad reads as the default, never raises):
`start_day` an ISO `YYYY-MM-DD` string; `min_sessions` `_count(minimum=1)`;
`alpha` strictly between 0 and 1; `min_effect_pp` finite and `>= 0`; `order` a list
holding each of `morning`, `midday`, `afternoon` exactly once; `permutations`
`_count(minimum=100)`; `seed` `_count(minimum=0)`; `min_sign_agreement`
`_fraction(allow_zero=False)`; `agree_min_sessions` `_count(minimum=1)`.
`regime_order` returns a fresh list.

- [ ] **Step 4: Add `[regime]` to `config/scalp.toml`** with the Global Constraints
  values and this comment at the top of the section:

```toml
[regime]
# The declared net-gamma regime study of the 15-minute calls (design
# docs/plans/2026-10-25-scalp-gamma-regime-study-design.md). Every value below was
# DECLARED on 2026-10-25, before any outcome of the test window was read. Changing
# one after reading the result is a new study, declared and run as one.
```

  and a one-line comment above each key saying what it is (the design's §3 wording).

- [ ] **Step 5: Add the nine Fields** under `Section("Gamma regime study", "The declared test of whether the dealer gamma regime changes how often the 15-minute calls' long options reach their target. Declared 2026-10-25: change a value only to declare a new study.", (...))`.
  Kinds: `regime.start_day` `date`; `min_sessions` `int` (min 1, max 1000);
  `alpha` `float` (min 0.001, max 0.5, step 0.001); `min_effect_pp` `float`
  (unit "points", min 0, max 50, step 0.5); `order` `phrases`; `permutations` `int`
  (min 100, max 100000, step 100); `seed` `int` (min 0, max 2147483647, step 1);
  `min_sign_agreement` `fraction`; `agree_min_sessions` `int` (min 1, max 100).

- [ ] **Step 6: Run the config tests and the catalogue test**

Run: `$PY -m pytest shared/tests/test_scalp_config.py -q`
Expected: PASS.
Run: `cd webgui && $PY -m pytest tests/test_config_schema.py -q`
Expected: PASS (it fails on a TOML key the catalogue lacks, so this proves Step 5).

- [ ] **Step 7: Commit**

```bash
git add shared/scalp_config.py config/scalp.toml webgui/config_schema.py shared/tests/test_scalp_config.py
git commit -m "feat(scalp): the declared [regime] study settings"
```

---

### Task 2: The gap rule, the cells and the usable sessions

**Files:**
- Create: `services/options_svc/scalp_regime_study.py`
- Test: `services/options_svc/tests/test_scalp_regime_study.py`

**Interfaces:**
- Consumes: Task 1's accessors; `scalp_confirm._norm`, `scalp_confirm._wall_view`;
  `scalp_study._label`, `scalp_study._scale_key`; `scalp_config.intraday_block`,
  `scalp_config.early_close_days`, `scalp_config.confirm_max_age_min`.
- Produces:
  - `NEGATIVE = "negative"`, `POSITIVE = "positive"`, `LONG_ROLES = ("long_with", "long_against")`, `CREDIT_ROLES = ("credit_with", "credit_against")`.
  - `regime_of(snapshot: dict | None, symbol: str) -> str | None`
  - `regimes(readings: dict) -> dict` — `{(day, slot, symbol): "negative" | "positive" | None}` from `{(day, slot, symbol): reading_body}` (the reading's `"snapshot"` key).
  - `cells(outcomes: list[dict], scale: float, block: str, roles: tuple) -> dict` — `{(day, symbol, slot): [wins, n, timeouts]}` over labelled rows of that block, roles and multiplier. An outcome row is a dict with keys `day, slot, symbol, iv_scale, role, block, result`.
  - `usable_sessions(regime_map: dict, outcomes: list[dict]) -> list[str]` — sorted ISO days.

- [ ] **Step 1: Write the failing tests.** Add module helpers in the test file:
  `snap(symbol="$SPX", net_gex=-1e9, state="ok", age=1.0, untrusted=False)` returning
  `{"matrix": {symbol: {"spot": 100.0, "flip": 99.0, "call_wall": 105.0, "put_wall": 95.0, "net_gex": net_gex, "walls_untrusted": untrusted}}, "sources": {"matrix": {"state": state, "age_min": age}}}`,
  and `out(day, slot, symbol="$SPX", role="long_with", result="win", scale=1.0)` that
  fills `block` from `scalp_config.intraday_block(slot)`. Afternoon slots in tests are
  `"12:36"` and `"13:06"`; a midday slot is `"10:06"`.

```python
def test_regime_of_signs():
    assert rs.regime_of(snap(net_gex=-5e8), "$SPX") == rs.NEGATIVE
    assert rs.regime_of(snap(net_gex=5e8), "$SPX") == rs.POSITIVE

def test_regime_of_gaps():
    assert rs.regime_of(snap(net_gex=0.0), "$SPX") is None
    assert rs.regime_of(snap(net_gex=None), "$SPX") is None
    assert rs.regime_of(snap(net_gex=float("nan")), "$SPX") is None
    assert rs.regime_of(snap(untrusted=True), "$SPX") is None
    assert rs.regime_of(snap(state="stale"), "$SPX") is None
    assert rs.regime_of(snap(state="absent"), "$SPX") is None
    assert rs.regime_of(snap(age=6.0), "$SPX") is None          # > max_age_min 5
    assert rs.regime_of(None, "$SPX") is None
    assert rs.regime_of(snap(symbol="SPY"), "$SPX") is None     # no row for it

def test_regime_of_reads_a_backfilled_snapshot():
    assert rs.regime_of(snap(state="backfill", age=None), "$SPX") == rs.NEGATIVE

def test_regime_of_matches_symbol_without_dollar():
    assert rs.regime_of(snap(symbol="SPX"), "$SPX") == rs.NEGATIVE

def test_cells_count_labelled_long_rows_of_the_block():
    rows = [out("2026-10-21", "12:36", result="win"),
            out("2026-10-21", "12:36", role="long_against", result="timeout"),
            out("2026-10-21", "12:36", role="credit_with", result="win"),
            out("2026-10-21", "12:36", result="skip"),
            out("2026-10-21", "12:36", result="win", scale=1.25),
            out("2026-10-21", "10:06", result="win")]
    got = rs.cells(rows, 1.0, "afternoon", rs.LONG_ROLES)
    assert got == {("2026-10-21", "$SPX", "12:36"): [1, 2, 1]}

def test_usable_sessions_start_on_the_declared_day():
    regime = {("2026-10-20", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    rows = [out("2026-10-20", "12:36"), out("2026-10-21", "12:36")]
    assert rs.usable_sessions(regime, rows) == ["2026-10-21"]

def test_usable_sessions_skip_early_close_and_gaps():
    regime = {("2026-11-27", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-22", "12:36", "$SPX"): None}
    rows = [out("2026-11-27", "12:36"), out("2026-10-22", "12:36")]
    assert rs.usable_sessions(regime, rows) == []

def test_usable_sessions_ignore_the_0836_record():
    regime = {("2026-10-21", "08:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "08:36")]) == []

def test_day_without_afternoon_outcomes_is_not_usable():
    regime = {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "10:06")]) == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest services/options_svc/tests/test_scalp_regime_study.py -q`
Expected: FAIL, `ImportError` (no module `scalp_regime_study`).

- [ ] **Step 3: Implement.** `regime_of`: the matrix state must be `"ok"` with a
  finite `age_min <= confirm_max_age_min()`, or `"backfill"` (the extract applied the
  age when it read); the row is found by `_norm` over `snapshot["matrix"]`; a row whose
  `_wall_view(row)["walls_untrusted"]` is true, or whose `net_gex` is not a finite
  non-zero number, is a gap. A usable session: day `>= regime_start_day()`, not an
  early-close day, and at least one `(slot, symbol)` in the afternoon block with a
  non-gap regime AND at least one labelled long row at `iv_scale()`. Module docstring
  states the design link and the no-`prob_sources` rule.

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest services/options_svc/tests/test_scalp_regime_study.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/options_svc/scalp_regime_study.py services/options_svc/tests/test_scalp_regime_study.py
git commit -m "feat(scalp): the regime study's gap rule and usable sessions"
```

---

### Task 3: The effect, the permutation and the declared verdict

**Files:**
- Modify: `services/options_svc/scalp_regime_study.py`
- Test: `services/options_svc/tests/test_scalp_regime_study.py`

**Interfaces:**
- Consumes: Task 2's `regimes`, `cells`, `usable_sessions`.
- Produces:
  - `effect(cell_map: dict, regime_map: dict, symbols: tuple | None = None) -> float | None` — percentage points.
  - `permutation_p(cell_map: dict, regime_map: dict, sessions: list[str], observed: float, n: int, seed: int) -> float`
  - `evaluate(regime_map: dict, outcomes: list[dict], agreement: dict | None) -> dict` — the result (shape below).

**The statistic (design §3.3).** For each `(day, symbol)` in `cell_map`: the negative
share is the summed wins over summed n across that pair's slots whose regime is
`NEGATIVE`; likewise positive. The effect is `100 × (mean of the negative shares over
pairs that have one − mean of the positive shares over pairs that have one)`; `None`
when either side has no pair. `symbols` limits the pairs (for the per-symbol report).

**The permutation (design §3.3), exactly:**

```python
rng = random.Random(seed)
hits = 0
for _ in range(n):
    moved = rng.sample(sessions, len(sessions))
    fake = {}
    for a, b in zip(sessions, moved):
        for (day, slot, sym), reg in regime_map.items():
            if day == a:
                other = regime_map.get((b, slot, sym))
                fake[(a, slot, sym)] = other if (reg is not None and other is not None) else None
    e = effect(cell_map, fake)
    if e is not None and e >= observed - 1e-9:
        hits += 1
return hits / n
```

(Index `regime_map` by day once before the loop; the logic above is the contract.)

**`evaluate` result.** While `len(usable_sessions(...)) < regime_min_sessions()`:
`{"status": "collecting", "sessions": <count>, "needed": <min>, "first": <first day or None>, "start_day": <iso>}` and NOTHING else is computed. Otherwise:

```text
{"status": "ready",
 "sessions": [<the first min_sessions usable days>],
 "precondition": {"checked": bool, "passed": bool, "share": float|None, "sessions": int|None},
 "blocks": [ per block in regime_order():
   {"block": str, "tested": bool, "passed": bool, "reasons": [str],
    "effect_pp": {"<scale>": float|None for each iv_scales()},   # long roles
    "p": float|None, "negative_pairs": int, "positive_pairs": int,
    "reported": {"per_symbol": {sym: float|None}, "credit_pp": float|None,
                 "long_with_pp": float|None, "long_against_pp": float|None,
                 "timeout_share": {"negative": float|None, "positive": float|None}}} ]}
```

Only outcomes and regimes of the selected sessions are used. The precondition is
`passed` when `agreement` is a dict with `share >= regime_min_sign_agreement()` and
`sessions >= regime_agree_min_sessions()`; a `share` of `None` fails it. The first block is tested; each later one
only when the one before passed. An untested block has `tested False`, `passed False`,
`reasons ["not tested: <previous> did not pass"]`, and no effect or p computed. A
tested block passes when none of these reasons applies (the exact strings, listed in
`reasons` in this order):
`"effect undefined"`, `"p above alpha"`, `"effect below the floor"`,
`"not above 0 at every multiplier"`, `"precondition not checked"`,
`"precondition failed"`. The p-value runs only when the effect at `iv_scale()` is defined.

- [ ] **Step 1: Write the failing tests.** Add a fixture builder `world(n_days, neg_win, pos_win)`
  that makes `n_days` sessions from 2026-10-21 (weekdays, skipping 2026-11-26/27), each
  with `$SPX`, SPY, QQQ at afternoon slots `12:36` and `13:06`, alternating NEGATIVE and
  POSITIVE days per symbol (day i, symbol j: negative when `(i + j) % 2 == 0`), and long
  outcomes at every `iv_scales()` multiplier: on a negative slot both long roles win
  when `rng.random() < neg_win`, else timeout; same for positive with `pos_win`
  (`random.Random(1)`). It returns `(regime_map, outcomes)`. Pass `agreement={"share": 1.0, "sessions": 5}` unless a test says otherwise; monkeypatch `scalp_config.regime_permutations` to `lambda: 300`.

```python
def test_effect_is_negative_share_minus_positive_share():
    regime = {("d1", "12:36", "$SPX"): rs.NEGATIVE, ("d1", "13:06", "$SPX"): rs.POSITIVE,
              ("d2", "12:36", "$SPX"): rs.NEGATIVE}
    cell_map = {("d1", "$SPX", "12:36"): [2, 2, 0], ("d1", "$SPX", "13:06"): [0, 2, 2],
                ("d2", "$SPX", "12:36"): [1, 2, 1]}
    assert rs.effect(cell_map, regime) == pytest.approx(75.0)   # mean(1.0, 0.5) - 0.0

def test_effect_undefined_when_one_side_is_empty():
    regime = {("d1", "12:36", "$SPX"): rs.NEGATIVE}
    assert rs.effect({("d1", "$SPX", "12:36"): [1, 2, 0]}, regime) is None

def test_permutation_is_deterministic_and_bounded():
    regime, rows = world(12, 0.7, 0.3)
    sessions = sorted({d for d, _, _ in regime})
    cm = rs.cells(rows, 1.0, "afternoon", rs.LONG_ROLES)
    obs = rs.effect(cm, regime)
    a = rs.permutation_p(cm, regime, sessions, obs, 200, 5)
    assert a == rs.permutation_p(cm, regime, sessions, obs, 200, 5)
    assert 0.0 <= a <= 1.0

def test_collecting_shows_nothing_but_the_count():
    regime, rows = world(39, 0.9, 0.1)
    got = rs.evaluate(regime, rows, {"share": 1.0, "sessions": 5})
    assert got == {"status": "collecting", "sessions": 39, "needed": 40,
                   "first": "2026-10-21", "start_day": "2026-10-21"}

def test_a_strong_afternoon_effect_passes_and_midday_is_tested_next():
    regime, rows = world(40, 0.8, 0.2)
    got = rs.evaluate(regime, rows, {"share": 1.0, "sessions": 5})
    first, second = got["blocks"][0], got["blocks"][1]
    assert first["block"] == "afternoon" and first["passed"] and first["reasons"] == []
    assert second["block"] == "midday" and second["tested"]
    assert second["reasons"] == ["effect undefined"]       # the world has no midday rows
    assert got["blocks"][2]["reasons"] == ["not tested: midday did not pass"]

def test_no_effect_fails_and_stops_the_order():
    regime, rows = world(40, 0.0, 0.0)          # every long times out: effect 0, p 1.0
    blocks = rs.evaluate(regime, rows, {"share": 1.0, "sessions": 5})["blocks"]
    assert blocks[0]["reasons"] == ["p above alpha", "effect below the floor",
                                   "not above 0 at every multiplier"]
    assert blocks[1]["reasons"] == ["not tested: afternoon did not pass"]

def test_small_effect_fails_the_floor(monkeypatch):
    monkeypatch.setattr(scalp_config, "regime_min_effect_pp", lambda: 99.0)
    regime, rows = world(40, 0.8, 0.2)
    assert "effect below the floor" in rs.evaluate(regime, rows, {"share": 1.0, "sessions": 5})["blocks"][0]["reasons"]

def test_precondition_missing_or_failed_blocks_a_pass():
    regime, rows = world(40, 0.8, 0.2)
    assert "precondition not checked" in rs.evaluate(regime, rows, None)["blocks"][0]["reasons"]
    low = rs.evaluate(regime, rows, {"share": 0.9, "sessions": 5})
    assert "precondition failed" in low["blocks"][0]["reasons"]

def test_a_multiplier_at_or_below_zero_fails():
    regime, rows = world(40, 0.8, 0.2)
    rows = [dict(r, result="timeout") if r["iv_scale"] == 1.5 else r for r in rows]
    assert "not above 0 at every multiplier" in rs.evaluate(regime, rows, {"share": 1.0, "sessions": 5})["blocks"][0]["reasons"]

def test_sessions_after_the_minimum_are_ignored():
    regime, rows = world(40, 0.8, 0.2)
    base = rs.evaluate(regime, rows, {"share": 1.0, "sessions": 5})
    more_regime, more_rows = world(45, 0.8, 0.2)
    later = {k: v for k, v in more_regime.items() if k[0] > base["sessions"][-1]}
    flipped = [dict(r, result="win") for r in more_rows if r["day"] > base["sessions"][-1]]
    again = rs.evaluate({**regime, **later}, rows + flipped, {"share": 1.0, "sessions": 5})
    assert again == base
```

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest services/options_svc/tests/test_scalp_regime_study.py -q`
Expected: the new tests FAIL with `AttributeError` (`effect` not defined).

- [ ] **Step 3: Implement `effect`, `permutation_p`, `evaluate`** as specified above.
  Keys of `effect_pp` are `str(scalp_study._scale_key(s))`. `timeout_share` is the
  timeouts over n of the long rows, pooled by regime across the selected sessions.

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest services/options_svc/tests/test_scalp_regime_study.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/options_svc/scalp_regime_study.py services/options_svc/tests/test_scalp_regime_study.py
git commit -m "feat(scalp): the regime study's effect, permutation and declared verdict"
```

---

### Task 4: The loaders and the report

**Files:**
- Modify: `services/options_svc/scalp_regime_study.py` (two loaders)
- Create: `tools/scalp_regime_report.py`
- Test: `services/options_svc/tests/test_scalp_regime_study.py`

**Interfaces:**
- Consumes: Task 3's `evaluate`.
- Produces:
  - `load_readings(conn: sqlite3.Connection) -> dict` — `{(day, slot, symbol): body}` from `intraday_readings` (the same shape as `scalp_store.readings_all`).
  - `load_outcomes(conn: sqlite3.Connection) -> list[dict]` — every `intraday_outcomes` row as a dict with the seven keys Task 2 names.
  - In the tool: `render(db_path, agreement_path=None, today=None) -> str` and `main(argv=None) -> int` with usage `scalp_regime_report.py [path/to/scalp.db] [--agreement FILE]` (the path defaults to `repo_paths.SCALP_DB`).

**Exact copy the report prints** (tests assert on these):
- collecting: `# Gamma regime study - {today ISO}`, then
  `Collecting: {sessions} of {needed} usable sessions since {start_day}. First usable session: {first or "none yet"}.`
  then `Nothing else is shown until the {needed}th usable session is graded (design §3.4).`
- ready: the same heading; `Sessions: {first} to {last} ({n}).`; the precondition line
  (`Precondition: passed ({share:.2%} of slots agree over {sessions} sessions).`,
  `Precondition not checked: no block can pass.` or
  `Precondition failed ({share:.2%} of slots agree): no block can pass.`); per block,
  in order, `**{block}: PASS**` / `**{block}: FAIL** ({reasons joined by "; "})` /
  `**{block}: not tested** ({reason})`, then for a tested block a table of the effect at
  each multiplier, the p-value, the pair counts, and a `Reported, never decided` table of
  `reported`. Every FAIL is followed by:
  `A fail does not show there is no effect, only that it is not as large as it first looked.`
- Numbers use two decimals (`f"{x:+.2f} pp"`, p as `f"{p:.4f}"`); `-` for `None`.

- [ ] **Step 1: Write the failing tests.** Seed a real-schema store with
  `scalp_store.save_reading` and direct `INSERT`s into `intraday_outcomes` from Task 3's
  `world`; load the tool with `importlib` as `test_scalp_intraday_study._report` does.

```python
def test_loaders_round_trip(tmp_path):
    db = seed_store(tmp_path, 3, 0.8, 0.2)
    conn = sqlite3.connect(db)
    readings, rows = rs.load_readings(conn), rs.load_outcomes(conn)
    assert len({k[0] for k in readings}) == 3
    assert set(rows[0]) == {"day", "slot", "symbol", "iv_scale", "role", "block", "result"}

def test_report_while_collecting_prints_only_the_count(tmp_path):
    db = seed_store(tmp_path, 12, 0.9, 0.1)
    text = _regime_report().render(db, today=dt.date(2026, 11, 6))
    assert "Collecting: 12 of 40 usable sessions since 2026-10-21." in text
    assert " pp" not in text and "PASS" not in text and "FAIL" not in text

def test_report_ready_prints_verdicts_in_order(tmp_path):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    agree = tmp_path / "agree.json"
    agree.write_text(json.dumps({"share": 1.0, "sessions": 5}))
    text = _regime_report().render(db, agreement_path=agree)
    assert text.index("**afternoon: PASS**") < text.index("**midday:") < text.index("**morning:")

def test_report_without_agreement_cannot_pass(tmp_path):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    text = _regime_report().render(db)
    assert "Precondition not checked: no block can pass." in text
    assert "A fail does not show there is no effect, only that it is not as large as it first looked." in text

def test_report_ignores_prob_sources(tmp_path):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    before = _regime_report().render(db, today=dt.date(2026, 12, 20))
    flipped = [("intraday", day, slot, sym, opposite_snapshot(sym))
               for (day, slot, sym) in readings_keys(db)]
    scalp_store.save_prob_sources(flipped, db_path=db)
    assert _regime_report().render(db, today=dt.date(2026, 12, 20)) == before

def test_report_opens_the_store_read_only(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        _regime_report().render(missing)
    assert not missing.exists()
```

  (`seed_store`, `readings_keys` and `opposite_snapshot` are small helpers in the test
  file. `seed_store` writes each reading as `{"snapshot": ...}` and each outcome row
  with `structure` `"long_call"`, `both_in_bar` 0 and the other optional columns
  NULL; the last returns `snap(symbol=sym, net_gex=+1e9)` when the stored one is
  negative and the reverse.)

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest services/options_svc/tests/test_scalp_regime_study.py -q -k "loader or report"`
Expected: FAIL (`load_readings` not defined; the tool file does not exist).

- [ ] **Step 3: Implement the loaders and the tool.** The tool's docstring follows
  `scalp_intraday_report.py`'s: what it reads, read-only, the run command
  (`.venv/bin/python tools/scalp_regime_report.py [scalp.db] [--agreement agree.json]`),
  and that it shows nothing but the count before the minimum. `render` reads the
  agreement file as JSON when given (a missing or unreadable file is "not checked").

- [ ] **Step 4: Run the whole new test file**

Run: `$PY -m pytest services/options_svc/tests/test_scalp_regime_study.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/options_svc/scalp_regime_study.py tools/scalp_regime_report.py services/options_svc/tests/test_scalp_regime_study.py
git commit -m "feat(scalp): the regime study's read-only report"
```

---

### Task 5: The agreement check

**Files:**
- Create: `tools/scalp_regime_agree.py`
- Test: `tests/test_scalp_regime_agree.py` (beside `tests/test_scalp_prob_backfill.py`)

**Interfaces:**
- Consumes: `scalp_regime_study.regime_of`, `load_readings`; `tools.scalp_prob_backfill._read_records` (the JSONL reader, reused rather than copied); Task 1's `regime_start_day`, `regime_min_sign_agreement`, `regime_agree_min_sessions`.
- Produces:
  - `compare(records: list, readings: dict) -> dict` — `records` as `_read_records` returns them (`[((kind, day, slot, symbol), snapshot), ...]`); returns `{"sessions": int, "slots": int, "agreeing": int, "share": float | None, "days": [iso], "disagreeing": [[day, slot, symbol, live, backfill], ...], "passed": bool}`.
  - `main(argv=None) -> int` with `--in FILE [FILE ...] --db PATH --out FILE.json`; prints a one-paragraph summary and writes `compare`'s dict as JSON. Exit 0 whatever the result (a failed check is a finding, not an error); exit 2 on a bad argument or unreadable file.

**Rules.** A slot is compared when the record is `kind == "intraday"`, its day is
`>= regime_start_day()`, its slot has a block, and both `regime_of(backfill_snapshot,
symbol)` and `regime_of(live_reading["snapshot"], symbol)` are non-gap. `sessions` is
the count of distinct compared days; `share` is `agreeing / slots` (`None` at 0 slots);
`passed` is `share >= regime_min_sign_agreement() and sessions >= regime_agree_min_sessions()`.
The live store is opened read-only (`?mode=ro`) and only `intraday_readings` is read:
no outcome table is touched.

- [ ] **Step 1: Write the failing tests**

```python
def test_compare_counts_agreement():
    live = {("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(net_gex=-1e9)},
            ("2026-10-21", "13:06", "$SPX"): {"snapshot": snap(net_gex=-1e9)}}
    recs = [(("intraday", "2026-10-21", "12:36", "$SPX"), snap(net_gex=-2e9, state="backfill", age=None)),
            (("intraday", "2026-10-21", "13:06", "$SPX"), snap(net_gex=3e9, state="backfill", age=None))]
    got = agree.compare(recs, live)
    assert (got["slots"], got["agreeing"], got["sessions"]) == (2, 1, 1)
    assert got["share"] == 0.5 and got["passed"] is False
    assert got["disagreeing"] == [["2026-10-21", "13:06", "$SPX", "negative", "positive"]]

def test_compare_skips_gaps_morning_and_in_sample_days():
    live = {("2026-10-20", "12:36", "$SPX"): {"snapshot": snap()},
            ("2026-10-21", "08:36", "$SPX"): {"snapshot": snap()},
            ("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(untrusted=True)}}
    recs = [(("intraday", "2026-10-20", "12:36", "$SPX"), snap(state="backfill", age=None)),
            (("morning", "2026-10-21", "08:36", "$SPX"), snap(state="backfill", age=None)),
            (("intraday", "2026-10-21", "12:36", "$SPX"), snap(state="backfill", age=None))]
    got = agree.compare(recs, live)
    assert got["slots"] == 0 and got["share"] is None and got["passed"] is False

def test_compare_passes_at_the_declared_thresholds():
    days = ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]
    live = {(d, "12:36", "$SPX"): {"snapshot": snap()} for d in days}
    recs = [(("intraday", d, "12:36", "$SPX"), snap(state="backfill", age=None)) for d in days]
    got = agree.compare(recs, live)
    assert got["sessions"] == 5 and got["share"] == 1.0 and got["passed"] is True

def test_main_writes_the_result_and_never_writes_the_store(tmp_path):
    db = tmp_path / "scalp.db"
    scalp_store.save_reading("2026-10-21", "12:36", "$SPX", {"snapshot": snap()}, db_path=db)
    before = db.read_bytes()
    jsonl = tmp_path / "h.jsonl"
    jsonl.write_text(json.dumps({"kind": "intraday", "day": "2026-10-21", "slot": "12:36",
                                 "symbol": "$SPX", "snapshot": snap(state="backfill", age=None)}) + "\n")
    out = tmp_path / "agree.json"
    assert agree.main(["--in", str(jsonl), "--db", str(db), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["slots"] == 1
    assert db.read_bytes() == before
```

  (`snap` is the same helper as Task 2's; copy it into this test file.)

- [ ] **Step 2: Run them to see them fail**

Run: `$PY -m pytest tests/test_scalp_regime_agree.py -q`
Expected: FAIL, `ImportError` (no `tools.scalp_regime_agree`).

- [ ] **Step 3: Implement the tool.** It puts the repo root on `sys.path` before its
  imports, as the other tools do. Its docstring gives the two-step use: run
  `scalp_prob_backfill.py extract` over a gamma copy holding test-window sessions
  (e.g. `E:/TradingBackups/prod_2026-10-23_2001/options-scanner/gex_history.db`), then
  `scalp_regime_agree.py --in history.jsonl --db scalp.db --out agree.json`, and that it
  reads gamma and the live record only, never an outcome.

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest tests/test_scalp_regime_agree.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/scalp_regime_agree.py tests/test_scalp_regime_agree.py
git commit -m "feat(scalp): the regime study's live-vs-backfill agreement check"
```

---

### Task 6: Suites, changelog and handover

**Files:**
- Modify: `docs/CHANGELOG.md` (new entry at the top; the current top entry's `**Last updated:**` becomes `**Prior —**`, as the file does)

- [ ] **Step 1: Run the suites the change touches, each on its own**

Run: `$PY -m pytest services/options_svc -q`, `$PY -m pytest shared/tests -q`,
`$PY -m pytest tests -q`, `cd webgui && $PY -m pytest tests/test_config_schema.py -q`.
Expected: no failing test, and the failing and skipped SETS no different from `main`'s.

- [ ] **Step 2: Run the report and the agreement check on the real backup, read-only**

Run: `$PY tools/scalp_regime_report.py "E:/TradingBackups/prod_2026-10-23_2001/services/options_svc/data/scalp.db"`
Expected: `Collecting: 3 of 40 usable sessions since 2026-10-21.` (10-21 to 10-23), and
no effect figure anywhere in the output.

Run: `$PY tools/scalp_prob_backfill.py extract --gamma "E:/TradingBackups/prod_2026-10-23_2001/options-scanner/gex_history.db" --out <scratchpad>/regime_hist.jsonl`, then
`$PY tools/scalp_regime_agree.py --in <scratchpad>/regime_hist.jsonl --db "E:/TradingBackups/prod_2026-10-23_2001/services/options_svc/data/scalp.db" --out <scratchpad>/agree.json`
Expected: a summary over at most 3 sessions with `passed: false` (fewer than the 5
required). Record its share in the CHANGELOG entry: the first look at agreement.

- [ ] **Step 3: Write the CHANGELOG entry**: what shipped (study module, report,
  agreement check, `[regime]`), that nothing on the page or in the chance model
  changed, the two Step 2 results, and what is owed: the agreement check rerun once
  a backup holds 5+ test-window sessions, then the one read at the 40th usable
  session (about 2026-12-17).

- [ ] **Step 4: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs(changelog): the declared gamma regime study, phase 1"
```
