# Strategy Finder — the whole chain, every expiry: implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development in this session) to implement this plan
> task by task.

**Goal:** The Strategy Finder scans the whole option chain by default, builds every
structure on every listed expiry, flags (not drops) candidates that hold through
earnings, keeps its spinner up until the answer lands, and shows the spot price on
every answer — including an empty one.

**Architecture:** All scan logic stays in Tier 2 (`services/options_svc/compute.py`,
`handlers.py`). The builders in `options-scanner/strategy_scanner.py` are NOT changed:
"every expiry" is a compute-level loop over chain slices. Two new `swing_scan`
keywords (`every_expiry`, `earnings_mode`) default to today's behaviour so the Income
Window is untouched. Tier 1 changes are the pure `finder_view.py` plus wiring in
`swing.py`.

**Design:** [`2026-09-14-strategy-finder-whole-chain-design.md`](2026-09-14-strategy-finder-whole-chain-design.md)

**Tech stack:** Python 3.11, NiceGUI, pytest, fakeredis bus.

---

## Ground rules for every task

- **TDD.** Write the failing test, run it, see it fail for the right reason, implement,
  see it pass.
- **Never weaken an existing assertion** to make a test pass. If an existing test
  genuinely pins behaviour this design changes (e.g. the *Any* preset's 0–120), change
  it only where the design says so, and say which in the commit message.
- **Existing tests stay green unmodified** unless named in the task.
- Tier 1 (`webgui/`) imports no `services.*` and no engine; Tailwind classes only, no
  `.style()`.
- A missing reading is `None`, never 0. Never print a number you did not read.
- Commands (Windows worktree; the venv is the main checkout's):

  ```bash
  PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
  "$PY" -m pytest -q services/options_svc/tests/<file>.py          # from the worktree root
  (cd webgui && "$PY" -m pytest -q tests/<file>.py)
  "$PY" -m ruff check services/options_svc webgui
  ```
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- The branch already carries two unpromoted Strategy Finder commits (`125f96a` scan bar
  follows the cached scan, `1a8b46b` Strikes column). Build on top of them.

---

### Task 1: Grouped chain fetch, and no upper DTE limit

**Files:**
- Modify: `services/options_svc/compute.py` (new `SCAN_RUN_EXPIRIES`, `SCAN_FETCH_WORKERS`,
  `_NO_DTE_MAX`, `scan_expiry_runs`, `merge_raw_chains`, `fetch_scan_chain`; `swing_scan`
  uses it and accepts `dte_max=None`)
- Test: `services/options_svc/tests/test_scan_chain_fetch.py` (new)

**Step 1: Write the failing tests**

```python
"""The Strategy Finder's whole-chain fetch (2026-09-14).

One /chains call for SPY's whole chain timed out at the proxy's 30 s; groups of 8
consecutive expiries, 4 at a time, returned it in 6.5 s ($SPX's 56 expiries in 11 s).
"""
import datetime as dt

from services.options_svc import compute

TODAY = dt.date.today()


def _d(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


def test_runs_hold_at_most_eight_consecutive_expiries():
    exps = [_d(i) for i in range(1, 21)]                 # 20 listed
    runs = compute.scan_expiry_runs(exps, dte_max=None)
    assert [len(r) for r in runs] == [8, 8, 4]
    assert [e for r in runs for e in r] == exps


def test_runs_stop_at_dte_max_plus_the_two_day_slack_and_start_today():
    exps = [_d(0), _d(5), _d(30), _d(31), _d(32), _d(33)]
    runs = compute.scan_expiry_runs(exps, dte_max=30)
    assert [e for r in runs for e in r] == [_d(0), _d(5), _d(30), _d(31), _d(32)]


def test_no_dte_max_takes_every_listed_expiry():
    exps = [_d(1), _d(400), _d(2000)]
    assert [e for r in compute.scan_expiry_runs(exps, None) for e in r] == exps


def test_raw_merge_unions_expiry_maps_and_keeps_top_level_fields():
    a = {"underlyingPrice": 540.0, "volatility": 22.0,
         "callExpDateMap": {"x:1": {"1.0": []}}, "putExpDateMap": {"x:1": {"1.0": []}}}
    b = {"underlyingPrice": 541.0,
         "callExpDateMap": {"y:9": {"2.0": []}}, "putExpDateMap": {}}
    out = compute.merge_raw_chains([a, None, b])
    assert out["underlyingPrice"] == 540.0 and out["volatility"] == 22.0
    assert set(out["callExpDateMap"]) == {"x:1", "y:9"}
    assert set(out["putExpDateMap"]) == {"x:1"}
    assert a["callExpDateMap"] == {"x:1": {"1.0": []}}      # inputs not mutated


def test_merge_of_nothing_is_None():
    assert compute.merge_raw_chains([None, None]) is None


def test_fetch_groups_runs_and_counts_a_failed_one(monkeypatch):
    exps = [_d(i) for i in range(1, 18)]                 # 17 -> runs of 8, 8, 1
    monkeypatch.setattr(compute, "option_expirations", lambda api: exps)
    seen = []

    def _fetch(client, symbol, from_date=None, to_date=None):
        seen.append((str(from_date), str(to_date)))
        if str(from_date) == exps[8]:
            return None                                   # the second run fails
        return {"underlyingPrice": 1.0,
                "callExpDateMap": {f"{from_date}:1": {}}, "putExpDateMap": {}}

    monkeypatch.setattr(compute.se, "fetch_option_chain", _fetch)
    chain, failed = compute.fetch_scan_chain("NVDA", dte_max=None)
    assert sorted(seen) == sorted([(exps[0], exps[7]), (exps[8], exps[15]),
                                   (exps[16], exps[16])])
    assert failed == 8                                    # expiries, not runs
    assert chain is not None and len(chain["callExpDateMap"]) == 2


def test_no_expiration_list_falls_back_to_the_single_fetch(monkeypatch):
    monkeypatch.setattr(compute, "option_expirations", lambda api: [])
    seen = []
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None:
                        seen.append((from_date, to_date)) or {"underlyingPrice": 1.0})
    chain, failed = compute.fetch_scan_chain("SPY", dte_max=30)
    assert seen == [(TODAY, TODAY + dt.timedelta(days=32))] and failed == 0
    seen.clear()
    compute.fetch_scan_chain("SPY", dte_max=None)
    assert seen == [(TODAY, None)]


def test_an_expiration_list_that_raises_falls_back_rather_than_failing(monkeypatch):
    def _boom(api):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(compute, "option_expirations", _boom)
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None:
                        {"underlyingPrice": 1.0})
    chain, failed = compute.fetch_scan_chain("SPY", dte_max=30)
    assert chain == {"underlyingPrice": 1.0} and failed == 0


def test_every_run_failing_is_no_chain(monkeypatch):
    monkeypatch.setattr(compute, "option_expirations", lambda api: [_d(1), _d(2)])
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: None)
    chain, failed = compute.fetch_scan_chain("SPY", dte_max=None)
    assert chain is None and failed == 2


def test_swing_scan_accepts_no_dte_max(monkeypatch):
    """``dte_max=None`` reaches the fetch as None and every in-range filter as no
    bound; a 900-day expiry is inside the window."""
    seen = {}
    monkeypatch.setattr(compute, "fetch_scan_chain",
                        lambda symbol, dte_max: (seen.setdefault("dte_max", dte_max),
                                                 (None, 0))[1])
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: {"last": 540.0})
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1)
    assert seen["dte_max"] is None and out["signals"] == []
```

**Step 2: Run — expect `AttributeError: module ... has no attribute 'scan_expiry_runs'`.**

**Step 3: Implement** (beside the lazy-expirations helpers, ~line 6940)

```python
# ── the Strategy Finder's whole-chain fetch (2026-09-14) ─────────────────────
# One /chains call for SPY's whole chain timed out at the proxy's 30 s. Groups of
# consecutive expiries, fetched a few at a time, returned SPY's 34 expiries in
# 6.5 s and $SPX's 56 in 11 s (measured on prod). Every swing_scan caller rides
# this path; the Income Window's 30-45 DTE window is one or two runs.
SCAN_RUN_EXPIRIES = 8
SCAN_FETCH_WORKERS = 4
# The in-range filters take a number; "no upper limit" is this, never a guess at
# the longest listed expiry.
_NO_DTE_MAX = 100_000


def scan_expiry_runs(expirations, dte_max, today=None):
    """Listed expiries from TODAY to today + dte_max + 2 (every one when dte_max is
    None), in runs of at most SCAN_RUN_EXPIRIES consecutive listed expiries.

    From today, not from dte_min: run_iv_analysis reads the near expiries for the
    IV and expected move, exactly as the single fetch delivered them."""
    today = today or _dt.date.today()
    last = None if dte_max is None else today + _dt.timedelta(days=int(dte_max) + 2)
    keep = [e for e in expirations or []
            if _dt.date.fromisoformat(e) >= today
            and (last is None or _dt.date.fromisoformat(e) <= last)]
    return [keep[i:i + SCAN_RUN_EXPIRIES] for i in range(0, len(keep), SCAN_RUN_EXPIRIES)]


def merge_raw_chains(chains):
    """RAW chains as one: the two expiry maps unioned, every other top-level field
    from the first chain that arrived. None when none did. Inputs not mutated."""
    got = [c for c in chains if c]
    if not got:
        return None
    out = {k: v for k, v in got[0].items() if k not in ("callExpDateMap", "putExpDateMap")}
    for key in ("callExpDateMap", "putExpDateMap"):
        merged = {}
        for c in got:
            merged.update(c.get(key) or {})
        out[key] = merged
    return out


def fetch_scan_chain(symbol, dte_max):
    """``(chain, expiries_failed)`` for a swing scan.

    Lists the expirations, fetches them in runs (see scan_expiry_runs) at most
    SCAN_FETCH_WORKERS at a time, and merges. A run that fails is COUNTED in
    expiries_failed, never hidden. No usable expiration list falls back to the
    single fetch this function replaced."""
    from services._parallel import parallel_map

    client = _proxy.schwab_py_client
    today = _dt.date.today()
    try:
        exps = option_expirations(symbol)
    except Exception:  # noqa: BLE001 - the single fetch is the fallback
        _degrade.degraded("options.scan_expirations")
        exps = []
    if not exps:
        to = None if dte_max is None else today + _dt.timedelta(days=int(dte_max) + 2)
        return se.fetch_option_chain(client, symbol, from_date=today, to_date=to), 0
    runs = scan_expiry_runs(exps, dte_max, today)
    got = parallel_map(
        lambda run: se.fetch_option_chain(client, symbol,
                                          from_date=_dt.date.fromisoformat(run[0]),
                                          to_date=_dt.date.fromisoformat(run[-1])),
        runs, workers=SCAN_FETCH_WORKERS)
    failed = sum(len(run) for run, chain in zip(runs, got) if not chain)
    return merge_raw_chains(got), failed
```

In `swing_scan`: replace the `se.fetch_option_chain(...)` call with
`chain, expiries_failed = fetch_scan_chain(symbol, dte_max)`, and right after it set
`hi = _NO_DTE_MAX if dte_max is None else dte_max`; pass **`hi`** everywhere the body
passes `dte_max` to a builder or `screen_spreads`. Add `"expiries_failed": expiries_failed`
to every returned dict (both early returns and the final one). Document `dte_max=None`
in the docstring.

⚠ The existing `test_compute.py` swing tests patch `se.fetch_option_chain` and leave the
real client's `get_option_expirations`; the pytest network guard makes it raise, so they
take the fallback — which is why the try/except above is required, not decorative.

**Step 4: Run the new file, then `test_compute.py`, `test_income_scan.py`,
`test_swing_earnings_gate.py`, `test_lazy_expirations.py` — all pass.**

**Step 5: Commit** — `feat(finder-svc): fetch a scan's chain in expiry groups; no upper DTE limit`

---

### Task 2: Build on every expiry (`every_expiry`)

**Files:**
- Modify: `services/options_svc/compute.py` (`chain_slice`, `listed_expiry_dtes`,
  `_build_every_expiry`; `swing_scan(..., every_expiry=False)`)
- Test: `services/options_svc/tests/test_every_expiry.py` (new)

**Step 1: Write the failing tests**

Build a synthetic 4-expiry chain (DTE 3, 10, 38, 400) with the `_swing_chain` leg shape
from `test_compute.py` (copy the helper; add `daysToExpiration`-style keys
`"YYYY-MM-DD:dte"`), and stub the non-chain inputs the way
`test_swing_scan_multistrategy_pipeline` does (quote, history, technicals,
`run_iv_analysis`), with `SWING_MIN_SCORE=0` and `SWING_EXCLUDED_GRADES=()`.

```python
def test_every_expiry_equals_each_builder_run_by_hand_on_each_expiry(scan_env):
    ssn = scan_env.ssn
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1,
                             families=("DIRECTIONAL",), every_expiry=True, payoff=False)
    want = []
    for exp, dte in compute.listed_expiry_dtes(scan_env.chain, 0, compute._NO_DTE_MAX):
        want += ssn.build_directional(compute.chain_slice(scan_env.chain, {exp}), "SPY",
                                      540.0, scan_env.atm_iv, dte, dte,
                                      put_band=(-0.2, -0.1), call_band=(0.1, 0.2))
    key = lambda s: (s["type"], s["expiration"], tuple(l["strike"] for l in s["legs"]))
    assert sorted(map(key, out["signals"])) == sorted(map(key, want))
    assert {s["expiration"] for s in out["signals"]} == {e for e, _ in
            compute.listed_expiry_dtes(scan_env.chain, 0, compute._NO_DTE_MAX)}


def test_default_mode_is_unchanged(scan_env):
    """every_expiry=False keeps today's nearest-expiry path exactly."""
    a = compute.swing_scan("SPY", 0, 120, -0.2, -0.1, 0.1, 0.2, 0.1, payoff=False)
    b = compute.swing_scan("SPY", 0, 120, -0.2, -0.1, 0.1, 0.2, 0.1, payoff=False,
                           every_expiry=False)
    assert a == b
    assert {s["expiration"] for s in a["signals"]
            if s["group"] == "DIRECTIONAL"} == {scan_env.nearest}


def test_calendars_take_each_front_of_at_least_seven_days(scan_env):
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1,
                             families=("CALENDAR",), every_expiry=True, payoff=False)
    fronts = {min(l["expiration"] for l in s["legs"]) for s in out["signals"]}
    assert scan_env.exp_by_dte[3] not in fronts           # under 7 DTE: none
    assert len(out["signals"]) == len({(s["type"], min(l["expiration"] for l in s["legs"]))
                                       for s in out["signals"]})   # no duplicates


def test_iron_condors_are_paired_within_each_expiry(scan_env, monkeypatch):
    calls = []
    real = compute.se.build_iron_condors
    monkeypatch.setattr(compute.se, "build_iron_condors",
                        lambda spreads, **k: calls.append({s["expiration"] for s in spreads})
                        or real(spreads, **k))
    compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1,
                       families=("NEUTRAL",), every_expiry=True, payoff=False)
    assert calls and all(len(exps) <= 1 for exps in calls)


def test_ids_stay_unique_across_expiries(scan_env):
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1,
                             every_expiry=True, payoff=False)
    ids = [s["id"] for s in out["signals"]]
    assert len(ids) == len(set(ids))


def test_chain_slice_keeps_only_the_named_expiries_and_top_level_fields(scan_env):
    exp = scan_env.nearest
    s = compute.chain_slice(scan_env.chain, {exp})
    assert s["underlyingPrice"] == scan_env.chain["underlyingPrice"]
    assert {k.split(":")[0] for k in s["callExpDateMap"]} == {exp}
    assert {k.split(":")[0] for k in s["putExpDateMap"]} == {exp}
```

**Step 2: Run — expect failures on the missing helpers / keyword.**

**Step 3: Implement**

```python
def chain_slice(chain, keep):
    """``chain`` with both expiry maps cut to the expiries named in ``keep``
    (``YYYY-MM-DD``). Top-level fields are shared, not copied."""
    out = dict(chain)
    for key in ("callExpDateMap", "putExpDateMap"):
        out[key] = {k: v for k, v in (chain.get(key) or {}).items()
                    if k.split(":")[0] in keep}
    return out


def listed_expiry_dtes(chain, dte_min, dte_max):
    """``[(expiry, dte)]`` for every expiry either map lists inside the window,
    nearest first. DTE is the chain key's own, as the builders read it."""
    seen = {}
    for key in ("callExpDateMap", "putExpDateMap"):
        for k in chain.get(key) or {}:
            exp, dte = k.split(":")[0], int(float(k.split(":")[1]))
            if dte_min <= dte <= dte_max:
                seen[exp] = dte
    return sorted(seen.items(), key=lambda kv: kv[1])
```

In `swing_scan`, when `every_expiry` is True, replace the single calls to the five
single-expiry builders and `build_calendars` with a loop over
`listed_expiry_dtes(chain, dte_min, hi)`:

- single-expiry builders: `chain_slice(chain, {exp})`, `dte_min=dte, dte_max=dte`
  (bands passed exactly as today);
- `build_calendars`: only when `dte >= ssn._MIN_FRONT_DTE`, on
  `chain_slice(chain, {e for e, d in expiries if d >= dte})` with `dte_min=dte, dte_max=hi`;
- `screen_spreads` still runs ONCE on the whole chain; NEUTRAL groups the spreads by
  `expiration` and calls `se.build_iron_condors` per group.

Keep `_tag_group` on every batch. The `every_expiry=False` branch is today's code,
untouched. Add `every_expiry` to the docstring's parameter notes.

**Step 4: Run the new file plus Task 1's list — all pass.**

**Step 5: Commit** — `feat(finder-svc): build every structure on every listed expiry (every_expiry)`

---

### Task 3: Earnings flag mode, and the handler turns both on

**Files:**
- Modify: `services/options_svc/compute.py` (`swing_scan(..., earnings_mode="drop")`)
- Modify: `services/options_svc/handlers.py` (`swing_scan` handler passes
  `every_expiry=True, earnings_mode="flag"`)
- Test: `services/options_svc/tests/test_swing_earnings_gate.py` (add tests; existing
  ones stay as they are)

**Step 1: Write the failing tests**

```python
def test_flag_mode_keeps_a_spanning_candidate_and_stamps_it(scan_env):
    report = (dt.date.today() + dt.timedelta(days=20)).isoformat()
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1,
                             earnings_date=report, earnings_mode="flag",
                             every_expiry=True, payoff=False)
    late = [s for s in out["signals"] if compute._latest_expiration(s) >= report]
    early = [s for s in out["signals"] if compute._latest_expiration(s) < report]
    assert late and all(s["spans_earnings"] is True and s["earnings_date"] == report
                        for s in late)
    assert all(not s.get("spans_earnings") for s in early)


def test_flag_mode_hands_screen_spreads_no_date(scan_env, monkeypatch):
    seen = {}
    real = compute.se.screen_spreads
    def _spy(*a, **k):
        seen["earnings_date"] = k.get("earnings_date")
        return real(*a, **k)
    monkeypatch.setattr(compute.se, "screen_spreads", _spy)
    compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1,
                       earnings_date="2026-12-01", earnings_mode="flag", payoff=False)
    assert seen["earnings_date"] is None


def test_a_calendar_is_judged_on_its_back_month_in_flag_mode(scan_env):
    """Front before the report, back after it: flagged, not clean."""
    ...  # build from scan_env: report between the 10-DTE front and 38-DTE back


def test_drop_mode_is_the_default_and_unchanged(scan_env):
    report = (dt.date.today() + dt.timedelta(days=20)).isoformat()
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1,
                             earnings_date=report, every_expiry=True, payoff=False)
    assert all(compute._latest_expiration(s) < report for s in out["signals"])
    assert all("spans_earnings" not in s for s in out["signals"])


def test_the_finder_handler_asks_for_every_expiry_and_flags(swing_seam, monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("upcoming", "2026-12-01"))
    handlers.swing_scan(Bus(fake=True), {"symbol": "SPY"})
    assert swing_seam["every_expiry"] is True
    assert swing_seam["earnings_mode"] == "flag"


def test_the_income_window_keeps_nearest_expiry_and_the_drop(monkeypatch):
    seen = {}
    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("upcoming", "2026-12-01"))
    monkeypatch.setattr(compute, "swing_scan",
                        lambda *a, **k: seen.update(k) or {"signals": [], "view": {}})
    compute.income_scan("SPY")
    assert seen.get("every_expiry", False) is False
    assert seen.get("earnings_mode", "drop") == "drop"
```

(`scan_env` is the fixture from Task 2 — move it to `services/options_svc/tests/conftest.py`
if both files need it; check that file does not already define a clashing name.)

**Step 2: Run — fail on the unknown keyword.**

**Step 3: Implement.** In `swing_scan`:

```python
    spread_earnings = earnings_date if earnings_mode == "drop" else None
    # ... pass spread_earnings to se.screen_spreads instead of earnings_date ...

    if earnings_date and earnings_mode == "flag":
        # The Strategy Finder shows a candidate that holds through a report and
        # TAGS it (operator decision 2026-09-14): its whole-chain scan would
        # otherwise end every single stock at its next report. Judged on the
        # LATEST leg, as the drop is.
        for s in signals:
            if se.check_earnings_conflict(earnings_date, _latest_expiration(s)):
                s["spans_earnings"], s["earnings_date"] = True, earnings_date
    elif earnings_date:
        signals = [...]   # today's drop, unchanged
```

Handler: `compute.swing_scan(**params, market_state=..., earnings_date=...,
every_expiry=True, earnings_mode="flag")`.

**Step 4: Run `test_swing_earnings_gate.py`, `test_income_scan.py`, `test_every_expiry.py`.**

**Step 5: Commit** — `feat(finder-svc): the Finder flags candidates that hold through earnings`

---

### Task 4: Every scan request gets an answer, carrying the spot

**Files:**
- Modify: `services/options_svc/compute.py` (quote read BEFORE the chain; `spot` and
  `chain_missing` on every result)
- Modify: `services/options_svc/handlers.py` (publish `spot`, `chain_missing`,
  `expiries_failed`; catch a raising scan and publish `error: True`)
- Test: `services/options_svc/tests/test_swing_answers.py` (new)

**Step 1: Write the failing tests**

```python
def test_no_chain_still_returns_the_spot(monkeypatch):
    monkeypatch.setattr(compute, "fetch_scan_chain", lambda symbol, dte_max: (None, 0))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote", lambda s: {"last": 540.0})
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1)
    assert out["spot"] == 540.0 and out["chain_missing"] is True and out["signals"] == []


def test_an_empty_scan_carries_the_spot(scan_env, monkeypatch):
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 101.0)        # cut everything
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1, payoff=False)
    assert out["signals"] == [] and out["spot"] == 540.0
    assert out["chain_missing"] is False


def test_no_quote_and_no_chain_spot_is_None_not_zero(monkeypatch):
    monkeypatch.setattr(compute, "fetch_scan_chain", lambda symbol, dte_max: (None, 0))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote", lambda s: {})
    out = compute.swing_scan("SPY", 0, None, -0.2, -0.1, 0.1, 0.2, 0.1)
    assert out["spot"] is None


def test_the_handler_publishes_spot_and_the_failed_expiry_count(monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan", lambda **k: {
        "signals": [], "view": {}, "filtered_out": 3, "vol_filtered": 0,
        "spot": 764.48, "chain_missing": False, "expiries_failed": 2})
    bus = Bus(fake=True)
    handlers.swing_scan(bus, {"symbol": "SPY", "dte_max": None})
    p = bus.cache_get(handlers.CACHE_SWING).payload
    assert p["spot"] == 764.48 and p["expiries_failed"] == 2
    assert p["chain_missing"] is False and p["params"]["dte_max"] is None
    assert "error" not in p


def test_a_scan_that_raises_still_answers_its_request(monkeypatch):
    """Otherwise nothing is published and the page waits out its ceiling."""
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    def _boom(**k):
        raise RuntimeError("builder bug")
    monkeypatch.setattr(compute, "swing_scan", _boom)
    bus = Bus(fake=True)
    handlers.swing_scan(bus, {"symbol": "SPY", "dte_min": 7})
    p = bus.cache_get(handlers.CACHE_SWING).payload
    assert p["error"] is True and p["signals"] == [] and p["symbol"] == "SPY"
    assert p["params"] == {"symbol": "SPY", "dte_min": 7}
```

**Step 2: Run — fail.**

**Step 3: Implement.** `swing_scan`: move `quote = _proxy.schwab_client.get_quote(symbol)`
above the fetch; `spot = quote.get("last")`, then `spot = spot or chain.get("underlyingPrice")`
once a chain exists. Both early returns include `"spot": spot or None`,
`"chain_missing": not chain`, `"expiries_failed": expiries_failed`; the final result
includes `"spot": spot, "chain_missing": False`. Handler: wrap the
`compute.swing_scan(...)` call in `try/except Exception` → `_degrade.degraded("options.swing_scan")`
and `result = {"signals": [], "view": {}, "filtered_out": 0, "vol_filtered": 0,
"error": True}`; the payload copies `spot`, `chain_missing`, `expiries_failed` with
`.get` (absent → `None`/`False`/`0`), and `error` only when set.

⚠ `test_swing_scan_empty_when_no_chain` / `_no_spot` assert field by field precisely so
new keys do not break them — do not change them.

**Step 4: Run the new file, `test_compute.py`, `test_swing_earnings_gate.py`,
`services/tests/test_no_silent_degrades.py`.**

**Step 5: Commit** — `feat(finder-svc): every scan request gets an answer, with the spot price`

---

### Task 5: Page vocabulary — presets, no limit, earnings, price, reasons (pure)

**Files:**
- Modify: `webgui/pages/options/finder_view.py`
- Test: `webgui/tests/test_finder_view.py`

**Changes and their tests:**

1. `EXPIRY_PRESETS` = `("1–2 wk",7,14) ("2–6 wk",14,42) ("1–3 mo",30,90)
   ("3–12 mo",90,365) ("1 yr+",365,None) ("All",0,None)`. `expiry_preset_for(lo, None)`
   matches a `None` upper bound; `DEFAULT_DTE = expiry_range_for("All")` → `(0, None)`.
   Tests: each preset round-trips; `expiry_preset_for(0, None) == "All"`;
   `expiry_preset_for(0, 120) is None` (the old *Any* is now a hand-typed range — the one
   existing assertion this design changes; name it in the commit).
2. `scan_controls_from`: an explicit `dte_max: None` (or a missing pair) → `(0, None)`
   when `dte_min` is missing/bad; a whole `dte_min` with `dte_max None` → `(lo, None)`.
   Update `test_scan_controls_default_with_nothing_cached` to expect `(0, None)` and add a
   `None`-upper test.
3. `earnings_text(sig)` → `"Earnings Nov 19"` when `sig["spans_earnings"]` and the date
   parses (reuse `_MONTHS`), else `None`. `card_facts` gains `"earnings"`; `finder_rows`
   gains `"earnings"`. Tests: stamped row, unstamped row, bad date → `None`.
4. `summary_facts`: `price` from `payload["spot"]`, falling back to the first row's
   `underlying_price`; **`"Price unavailable"`** when neither is a real number. Counts
   add `"N expirations could not be loaded"` when `expiries_failed` is truthy (singular
   for 1). Tests: zero signals + spot → price shown and `"0 ideas · 18 below the quality
   bar"`; no spot anywhere → `"Price unavailable"`; `spot` NaN → unavailable.
5. `no_data_label(payload)` names the symbol and price, in this precedence:
   `error` → *The scan for SPY failed. Check System Status and scan again.* ·
   `chain_missing` → *No option chain came back for SPY at $764.48.* · quality cut ·
   too cheap · built nothing — each *… for SPY at $764.48 …* (without a price:
   *… for SPY …*). One test per branch plus the no-price form.
6. `scan_timeout_text(symbol, seconds)` → `"Scanning SPY… 12 s"` for the elapsed count.

**Commit** — `feat(finder): presets reach the whole chain; earnings, price and reasons on every answer`

---

### Task 6: Page wiring — blank DTE max, the tag, the spinner until the answer

**Files:**
- Modify: `webgui/pages/options/swing.py`
- Test: `webgui/tests/test_options_swing.py`

**Changes and their tests:**

1. `scan_params`: blank/`None` DTE max → `"dte_max": None`; `int()` otherwise. DTE max
   `ui.number` gets `placeholder="no limit"` and no forced value. Test: *All* then Scan
   sends `dte_max: None`; typing 60 sends 60.
2. The expiry pill group now has six pills — keep it one wrapping row. Test: the six
   labels, *All* active on an empty cache.
3. Card: when `c["earnings"]`, a `BADGE_WARN` pill with that text beside the expiry.
   List: `_STRATEGY_SLOT` shows `props.row.earnings` as a small warn tag after the name
   (`v-if`). Tests: a stamped signal's card and row carry the text; an unstamped one does
   not.
4. **Spinner:** `SCAN_TIMEOUT_SEC = 180`. `scan_busy = _busy.build_busy(list_box, …,
   timeout=SCAN_TIMEOUT_SEC, elapsed_label=…)` driven by `fv.scan_timeout_text`; the
   one-shot `ui.timer` in `_request_scan` uses `SCAN_TIMEOUT_SEC`. Tests: after a scan,
   the busy handle's `tick()` 31 s later (monkeypatch `busy._time.monotonic`) leaves the
   spinner visible and the placeholders up; an answering payload hides it; a payload
   with `error: True` for this request hides it and the list says the error line;
   another symbol's payload does not.
5. Summary strip paints on a zero-idea answer with the price. Test: payload with
   `signals: []`, `spot: 764.48` → the strip shows `$764.48` and `0 ideas`.
6. `_set_no_data(fv.no_data_label(payload))` wherever the empty list is painted.

**Commit** — `feat(finder): blank DTE max is no limit; earnings tag; spinner holds until the answer`

---

### Task 7: Docs

**Files:** `webgui/page_help.py` (`/options/swing`), `docs/manuals/user-guide/user-guide.md`,
`docs/manuals/reference-guide/reference-guide.md`, `docs/webgui-routes.md`,
`docs/CHANGELOG.md` (new top entry; demote the redesign entry to *Prior —*), `CLAUDE.md`.

- The route table's Strategy Finder line: *every listed expiry, whole chain by default*.
- CLAUDE.md's earnings section ("Three scan paths; the gate was live on one"): the
  Finder now **flags** (`earnings_mode="flag"`) while the Market Scanner and Income
  Window drop — an invariant change, so it is edited in place, not appended.
- Manuals: the six presets, blank DTE max, the earnings tag, the spinner's count and
  3-minute ceiling, the price on an empty answer, the failed-expirations line.
- Rebuild: `"$PY" docs/manuals/build_docs.py`; commit only the manuals whose content
  changed (restore date-only rebuilds with `git checkout --`).
- Run `webgui/tests/test_page_help*.py` and the manuals catalogue tests.

**Commit** — `docs(finder): whole chain, every expiry, earnings tag, spinner and empty answers`

---

### Task 8: Verify

1. **Suites:** `options_svc` (whole), `webgui` (whole), `options-scanner` (whole, to prove
   no builder moved), `shared/tests`, repo-root `tests`; ruff. Compare the failing SET.
2. **Local page harness** (`scratchpad/harness/finder_harness.py`, temporary
   `.claude/launch.json` entry, reverted after): extend the synthetic chain to ~20
   expiries out to 700 DTE; check *All* scans every expiry, the spinner shows a count and
   ends on the answer, a forced handler exception ends it with the error line, a
   quality-cut-everything scan shows the price, and the list fits at 1372 px.
3. **Final whole-branch review** (subagent), then ask the operator to merge and promote.
4. **After promotion, during market hours** (Redis-driven on prod, `.env` sourced): SPY,
   NVDA, $SPX with *All* — report wall time, rows, `filtered_out`, payload KB, the DTE
   distribution of the rows and of the top four picks, and any `expiries_failed`. Retake
   gallery `image19`.
