# Income window + share inventory — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a two-sided 30–45 DTE `INCOME` scan on its own morning schedule, then
share inventory in the manual paper account so put assignment produces stock and
covered calls can be screened against cost basis.

**Architecture:** Phase A is a new `trade_type` threaded through the existing
`screen_spreads` (which already loops both expiry maps, so both sides come free),
published to a new `cache:options:income` view on a `config/sessions.toml` slot,
read by a new Tier-1 page. Phase B adds an `equity_lots` table that holds cash
converted to shares and **never** reserved buying power, so
`reconcile_buying_power` stays correct untouched.

**Tech Stack:** Python 3.11, pytest, SQLite, Redis (`shared.bus`, fakeredis under
pytest), FastAPI service (`options_svc`), NiceGUI Tier-1 pages, Tailwind-only
styling.

**Design:** [`2026-09-05-income-window-and-share-inventory-design.md`](2026-09-05-income-window-and-share-inventory-design.md)

---

## Before you start

Read the design doc. Then read these, because the plan assumes them:

- `CLAUDE.md` → **3-tier architecture** (Tier-1 import allow-list — the pages here
  may import only `nicegui`, `bus_client`, `pages.*`, and the listed `shared.*`),
  **UI styling standard** (Tailwind-only; `.style(` is banned and guarded), and
  **Tests** (compare the failing *set*, never the count).
- `services/options_svc/handlers.py` around the `CACHE_*` constants and
  `handle_command` — the publish + command patterns you will copy.
- `options-scanner/paper_account_db.py` — `reconcile_buying_power` and
  `roll_session_if_needed` in particular.

Run per-suite from the repo root or inside the app folder, one at a time — never
`pytest services`, which puts several hyphenated app dirs on `sys.path` at once and
re-triggers the documented `scoring` / `notifier` collisions.

```bash
.venv/bin/python -m pytest services/options_svc -q
```

⚠ **That path is the Linux (dev/prod VPS) layout.** If you are working in a
**Windows worktree**, there is no venv in the worktree at all and the interpreter
lives at the main checkout under `Scripts/`, so every command needs the absolute
path:

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q
```

`options-scanner` additionally needs `-p no:randomly`.

**Baselines measured on this branch before Phase A** (compare the failing *set*,
never the count — and compare the **skipped** set too, since which test self-skips
varies run to run):

| suite | baseline |
|---|---|
| `options-scanner` | **1215 passed, 2 skipped, 0 failed** |
| `services/options_svc` | **1358 passed, 0 failed** |

Commit after every task. Do not batch.

---

# Phase A — the income window

## Task 1: Verify the calibration bucket question

Open risk 1 in the design. Settle it before writing anything, because the answer
changes Task 3.

**Step 1: Read the mirror**

Run: `sed -n '1,80p' shared/calibration.py`

Look at `bucket_key`. Determine whether it enumerates `trade_type` values (so a
third one needs a bucket) or derives the key generically.

**Step 2: Read the guard**

Run: `.venv/bin/python -m pytest shared/tests/test_cross_tier_mirrors.py -q`

Expected: PASS (baseline, before any change).

**Step 3: Record the answer**

Append a short note under **Open risks** item 1 in the design doc saying which it
is, and — if a bucket is needed — exactly which files must gain the `INCOME` key.

**Step 4: Commit**

```bash
git add docs/plans/2026-09-05-income-window-and-share-inventory-design.md
git commit -m "docs: settle the calibration-bucket question for the INCOME window"
```

---

## Task 2: Extend the earnings gate to INCOME

`screen_spreads` gates earnings on `trade_type == "SWING"` only. At 30–45 DTE a
straddled report is near-certain, so this must land **before** the window exists —
otherwise the first scan emits candidates over earnings.

**Files:**
- Modify: `options-scanner/scanner_engine.py` (the `if earnings_date and trade_type == "SWING":` branch inside `screen_spreads`)
- Test: `options-scanner/tests/test_scanner_engine.py`

**Step 1: Write the failing test**

```python
def test_income_window_skips_expirations_straddling_earnings():
    """At 30-45 DTE a straddled report is the common case, not the exception —
    the gate must cover INCOME, not just SWING."""
    chain = _chain_with_expiration(dte=35)      # existing helper in this file
    earnings = (_dt.date.today() + _dt.timedelta(days=20)).isoformat()

    sigs = se.screen_spreads(
        chain, "AAPL", 30, 45, -0.30, -0.20, 0.20, 0.30, 0.12,
        "INCOME", spot=100.0, earnings_date=earnings)

    assert sigs == []
```

If `_chain_with_expiration` does not exist under that name, use whatever chain
fixture the neighbouring `screen_spreads` tests build and set its DTE to 35.

**Step 2: Run it to verify it fails**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_scanner_engine.py::test_income_window_skips_expirations_straddling_earnings -q -p no:randomly`

Expected: FAIL — signals are returned, because the gate is SWING-only.

**Step 3: Minimal implementation**

Change the branch condition to cover both windows:

```python
            # Earnings avoidance (multi-day holds only). 0-DTE cannot straddle a
            # report; SWING and INCOME both can, and at 30-45 DTE it is the
            # common case rather than the exception.
            if earnings_date and trade_type in ("SWING", "INCOME"):
                if check_earnings_conflict(earnings_date, exp_str):
```

**Step 4: Run the file**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_scanner_engine.py -q -p no:randomly`

Expected: PASS, including every pre-existing SWING earnings test.

**Step 5: Commit**

```bash
git add options-scanner/scanner_engine.py options-scanner/tests/test_scanner_engine.py
git commit -m "feat(scanner): apply the earnings gate to the INCOME window too"
```

---

## Task 3: The `income_scan` compute function

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_income_scan.py` (create)

> ⚠ **Corrected 2026-09-05 after reading the code.** The first draft of this task
> invented a `_cash_secured_puts` builder, called the structure `NAKED_PUT`, and
> asserted `max_loss_total == strike × 100 × qty`. All three were wrong:
>
> * **The scan-side name is `SHORT_PUT`.** `NAKED_PUT` is the Calculator/rescue
>   spelling (`compute._SINGLE_STRATEGIES`); `strategy_scanner._DIRECTIONAL` and
>   the `ScanResult` contract both say `SHORT_PUT`. Do not introduce a third.
> * **The builder already exists.** `strategy_scanner.build_directional` emits
>   `SHORT_PUT` at `_SHORT_DELTA = 0.28` — inside the 0.20–0.30 band this window
>   wants. Reuse it with the INCOME DTE range; write no new builder.
> * **Collateral is already computed, and more correctly than the draft.** For a
>   short put `payoff_metrics` has no call legs, so `max_loss` is taken at S=0 —
>   `(strike − credit) × 100 + commission`, the true stock-to-zero risk,
>   commission-inclusive. The strike notional the draft asserted is both wrong
>   and worse.
>
> The row shape is therefore the **normalized single-leg contract**: `legs`,
> `type`, `dte`, `capital`, `max_loss`, `breakevens` (a list), `rr` (a ratio) —
> NOT the spread path's flat `short_strike` / `rr_pct` / `breakeven`. The
> `ScanResult` docstring warns about exactly this split; read it before writing
> assertions.

**Step 1: Write the failing tests**

`swing_scan` does its own I/O, so these tests monkeypatch the same seven seams the
existing `swing_scan` tests do. **Copy that setup from
`services/options_svc/tests/test_compute.py` (around line 145) rather than
inventing one** — it already stubs `se.fetch_option_chain`, `_proxy.schwab_client.get_quote`,
`se.fetch_price_history`, `se.calc_technicals`, `run_iv_analysis`,
`se.screen_spreads` and `se.build_iron_condors`, and its `_screen` stub records
the `kind` positional, which is exactly the argument this task changes.

```python
"""The INCOME window: 30-45 DTE, two-sided, plus the cash-secured put.

The two-sidedness is the point — see the design doc. A test that only asserts
PCS would pass on a long-only implementation, which is exactly the regression
this file exists to prevent.
"""
import services.options_svc.compute as compute


def test_income_scan_screens_as_INCOME_not_SWING(income_seams):
    """The trade_type reaches screen_spreads, which is what makes the earnings
    gate, the liquidity floor and the calibration bucket all key off INCOME."""
    compute.income_scan("AAPL")
    assert income_seams["screen"]["kind"] == "INCOME"


def test_income_scan_screens_the_30_45_window(income_seams):
    compute.income_scan("AAPL")
    assert (income_seams["screen"]["dte_min"],
            income_seams["screen"]["dte_max"]) == (30, 45)


def test_income_scan_emits_both_spread_sides(income_seams):
    kinds = {c["type"] for c in compute.income_scan("AAPL")["signals"]}
    assert "PCS" in kinds
    assert "CCS" in kinds


def test_income_scan_emits_the_cash_secured_put(income_seams):
    csps = [c for c in compute.income_scan("AAPL")["signals"]
            if c["type"] == "SHORT_PUT"]
    assert csps, "the cash-secured put is the whole point of a 30-45 DTE window"
    csp = csps[0]
    assert len(csp["legs"]) == 1
    assert csp["legs"][0]["side"] == "short"
    assert csp["legs"][0]["kind"] == "put"
    assert csp["capital"] > 0


def test_income_scan_drops_structures_this_window_does_not_want(income_seams):
    """A long call at 35 DTE is a different thesis and SHORT_CALL is undefined
    risk — neither should be ranked against the premium core."""
    kinds = {c["type"] for c in compute.income_scan("AAPL")["signals"]}
    assert not (kinds & {"LONG_CALL", "LONG_PUT", "SHORT_CALL",
                         "BULL_CALL", "BEAR_PUT"})


def test_swing_scan_defaults_are_unchanged(income_seams):
    """Nine existing call sites depend on these defaults. A default-args call
    must still screen as SWING and filter no structure."""
    compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10)
    assert income_seams["screen"]["kind"] == "SWING"
```

Build `income_seams` as a fixture returning the `calls` dict, with the `_screen`
stub extended to record `dte_min`/`dte_max` and to return **both** a PCS and a CCS
row so the two-sidedness assertion is real.

⚠ **Two vacuity traps here, and both are easy to fall into.**

1. `test_income_scan_drops_structures_this_window_does_not_want` passes trivially
   if the builders produced none of those types to begin with. Assert first — in
   the same test or a sibling — that an *unfiltered* call **does** produce them,
   or the test proves nothing.
2. `assert csp["capital"] > 0` needs the stubbed chain to carry a put near
   0.28 delta with a real mark, or `build_directional` returns nothing and the
   list-index raises rather than asserting. Verify the fixture yields a non-empty
   `signals` list before trusting any of these.

This repo has a documented incident where three tests passed only because every
fixture had drifted into a state that took the same early-out. Do not add a
fourth.

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_income_scan.py -q`

Expected: FAIL — `AttributeError: module ... has no attribute 'income_scan'`.

**Step 3: Implement**



> ⚠ **Corrected again — do not write a parallel scan function.** `swing_scan`
> (`compute.py:270`) is not a pure function over an injected chain: it fetches
> the chain, quote and price history itself, then derives `atm_iv`, `em_1sd` and
> the market `view`, guards a null chain and a null spot, scores, applies the
> quality cut, assigns ids and stamps `iv_rank`. That is ~60 lines of correct
> machinery including a **documented percent/decimal trap** in the `atm_iv`
> derivation. A second function taking `chain=`/`atm_iv=` would duplicate all of
> it, and this repo has been bitten badly enough by duplication (`clamp` nine
> times, `num` seven) that the plan should not add more.

**Extend `swing_scan` with two backward-compatible parameters, then make
`income_scan` a thin wrapper.**

```python
def swing_scan(symbol, dte_min, dte_max, put_d_min, put_d_max,
               call_d_min, call_d_max, min_cr_fraction, families=None,
               market_state=None, trade_type="SWING", structures=None) -> dict:
```

* `trade_type` is threaded to the **one** `se.screen_spreads(...)` call in the
  body, replacing the hardcoded `"SWING"`. This is what makes the earnings gate
  (task 2), the liquidity floor (above) and the calibration bucket all key off
  `INCOME`.
* `structures`, when given, keeps only candidates whose `type` is in it — applied
  **once, after building and before `score_all`**, so `filtered_out` still counts
  what the quality cut removed rather than what the window never wanted.

One filter handles both exclusions this window needs: `build_directional` emits
all four singles (only `SHORT_PUT` is wanted — a long call is a different thesis
and `SHORT_CALL` is undefined-risk), and the `VERTICAL` family brings debit
verticals along with the adapted credit spreads.

`adapt_credit_spread` **preserves every source field**, so an adapted spread keeps
`type` `"PCS"`/`"CCS"` while gaining `legs`. The filter is therefore uniform across
all three wanted structures.

Both parameters default to today's behaviour, so all nine existing `swing_scan`
call sites keep passing untouched. **Add a test that pins exactly that** — a
default-args call must still pass `"SWING"` to `screen_spreads` and filter nothing.

```python
# ── INCOME window (30-45 DTE) ────────────────────────────────────────────────
# A THIRD scan window beside 0-DTE and swing, on its own morning slot. Two-sided
# by construction: screen_spreads loops BOTH expiry maps out of the same chain
# object, so the CCS side costs no extra Schwab call. See
# docs/plans/2026-09-05-income-window-and-share-inventory-design.md.
INCOME_DTE_MIN = 30
INCOME_DTE_MAX = 45

# PCS/CCS are the two-sided premium core; SHORT_PUT is the cash-secured put.
# Everything else build_directional / the VERTICAL family emits is a different
# trade with a different thesis, so it is filtered rather than scored and ranked
# against these.
_INCOME_STRUCTURES = ("PCS", "CCS", "SHORT_PUT")


def income_scan(symbol, market_state=None) -> dict:
    """The 30-45 DTE income window for one symbol: PCS + CCS + cash-secured put.

    ⚠ Two row SHAPES land in one ranked list, exactly as ``swing_scan`` already
    produces: the adapted spreads carry BOTH the flat ``short_strike`` contract
    and ``legs``; ``SHORT_PUT`` carries only the normalized ``legs`` one. Readers
    must not assume either — see the ScanResult docstring.
    """
    pd_min, pd_max = _scanner_config.directional_delta_range()["PCS"]
    cd_min, cd_max = _scanner_config.directional_delta_range()["CCS"]
    return swing_scan(symbol, INCOME_DTE_MIN, INCOME_DTE_MAX,
                      pd_min, pd_max, cd_min, cd_max,
                      _scanner_config.min_credit_pct()["SWING"],
                      families=("VERTICAL", "DIRECTIONAL"),
                      trade_type="INCOME",
                      structures=_INCOME_STRUCTURES,
                      market_state=market_state)
```

⚠ The delta bands and credit floor above are a **first guess at the right
accessors, not verified**. Check `shared/scanner_config.py` for the real shapes —
`directional_delta_range()` and `min_credit_pct()` both exist but their keying
must be confirmed, and `min_credit_pct()["SWING"]` may be the 1–15 DTE floor
rather than one appropriate to 30–45 DTE. If the income window needs its own
credit floor, it belongs in `config/scanner.toml`, not as a literal.

Note the chain fetch inside `swing_scan` is bounded `today … dte_max + 2`, so a
45-DTE window pulls **one** chain of ~47 days — which is the ~23 calls/day the
design costed.

### ⚠ `INCOME` has no liquidity floor — add one, and guard the fail-open

Found while reviewing Task 2, verified in code. `LIQUIDITY_THRESHOLDS`
(`scanner_engine.py:448`) defines **only** `"0-DTE"` and `"SWING"`, and
`passes_liquidity_gate` (`:558`) does:

```python
    thresholds = LIQUIDITY_THRESHOLDS.get(trade_type)
    if thresholds is None:
        return True  # unknown trade type — don't filter
```

So an `INCOME` scan as of Task 2 runs with **no liquidity gate whatsoever**. That
matters more here than at any other horizon: a 30–45 DTE chain carries far more
dead strikes than a 0-DTE one, and an untradeable spread with a fat theoretical
credit is exactly what a premium screen must not surface.

Add an `INCOME` entry. Do **not** copy SWING's numbers unthinkingly — the two
differ in a specific way you should reason about and write down: a monthly strike
*accumulates* open interest but trades *less per day* than a weekly, so `min_oi`
should be at least SWING's while `min_volume` cannot be. Justify whatever you pick
in a comment.

Then close the fail-open, because the next window will hit it too:

```python
def test_every_scanned_trade_type_has_a_liquidity_floor():
    """passes_liquidity_gate fails OPEN on an unknown trade_type, so a window
    added without an entry here silently runs unfiltered — which is how INCOME
    shipped gateless in the first place."""
    assert set(se.SCANNED_TRADE_TYPES) <= set(se.LIQUIDITY_THRESHOLDS)
```

Introduce `SCANNED_TRADE_TYPES = ("0-DTE", "SWING", "INCOME")` beside the
thresholds dict as the single list of windows the scanner emits. This is the one
new constant the plan sanctions: it exists to make a silent fail-open loud, which
is not the same as a config knob nobody asked for.

⚠ Do **not** flip the `return True` to `return False`. Other callers pass trade
types this dict has never covered, and a blanket fail-closed would silently empty
them. The test is the guard; the default stays.

### Two more details to settle while implementing

Verify both in code rather than assuming:

1. **Scoring.** `swing_scan` runs its candidates through `ssc.score_all(...)` and
   then `_passes_swing_cut`. Decide whether the income window reuses
   `SWING_MIN_SCORE` or needs its own knob. Prefer **reusing** it unless you find
   a concrete reason not to — a second constant with the same value is a liability
   until the two genuinely diverge. If you do add one, it belongs in
   `config/scanner.toml` under `[scores]`, not as a literal.
2. **The earnings gate on the single.** `screen_spreads` gates earnings itself
   (task 2). `build_directional` does **not**. Apply `se.check_earnings_conflict`
   to the single's expiration too, or a 35-DTE cash-secured put sails straight over
   the report the spreads were just protected from. Add a test for this
   specifically — it is the easiest thing here to leave half-done.

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_income_scan.py -q`

Expected: PASS (4 tests).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_income_scan.py
git commit -m "feat(options): two-sided 30-45 DTE income scan with cash-secured puts"
```

---

## Task 4: The `IncomeScan` contract

**Files:**
- Modify: `shared/contracts/options.py`
- Test: `shared/contracts/tests/test_options.py`

**Step 1: Write the failing test**

```python
def test_income_scan_accepts_a_sparse_payload():
    """Candidates are heterogeneous (PCS/CCS/SHORT_PUT/covered calls), so the
    contract gates the ENVELOPE, not each row — same call the ScanResult
    docstring makes and for the same reason."""
    from shared.contracts.options import IncomeScan

    snap = IncomeScan(candidates=[{"type": "PCS"}, {"type": "SHORT_PUT"}],
                      scanned_symbols=2, ts="2026-09-05T14:00:00Z")
    assert len(snap.candidates) == 2


def test_income_scan_defaults_let_an_older_payload_validate():
    """Redis persists cache views across restarts; every added field needs a
    default or a pre-upgrade payload fails to load."""
    from shared.contracts.options import IncomeScan
    assert IncomeScan().candidates == []
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest shared/contracts/tests/test_options.py -q`

Expected: FAIL — `ImportError: cannot import name 'IncomeScan'`.

**Step 3: Implement**

```python
class IncomeScan(_Base):
    """The 30-45 DTE income window's published candidates.

    Rows are heterogeneous — two-leg credit spreads (PCS/CCS), cash-secured puts
    and covered calls all land in ONE jointly-ranked list — so ``candidates`` is
    modelled loosely as ``list[dict]``, the same judgement ``ScanResult`` makes.

    ⚠ The chain is deliberately absent. Publishing it here would repeat the
    cache:options:calc_chain incident (8.77 MB, 53% of all prod Redis string
    bytes) at a wider DTE.
    """

    candidates: list[dict] = []
    scanned_symbols: int = 0
    errors: list = []
    warnings: list = []
    ts: str | None = None
```

**Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest shared/contracts -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add shared/contracts/options.py shared/contracts/tests/test_options.py
git commit -m "feat(contracts): add IncomeScan for the 30-45 DTE window"
```

---

## Task 5: The `[slots.income]` schedule gate

**Files:**
- Modify: `config/sessions.toml`
- Modify: `services/options_svc/scheduler.py`
- Test: `services/options_svc/tests/test_income_slot.py` (create)

**Step 1: Write the failing tests**

```python
import datetime as _dt
from zoneinfo import ZoneInfo

import services.options_svc.scheduler as sch

CT = ZoneInfo("America/Chicago")


def _at(h, m, day="2026-09-08"):        # a Tuesday
    d = _dt.date.fromisoformat(day)
    return _dt.datetime(d.year, d.month, d.day, h, m, tzinfo=CT)


def test_income_slot_fires_once_per_trading_day():
    ran = set()
    slot = sch.income_slot_due(_at(8, 45), ran)
    assert slot is not None
    ran.add(("2026-09-08", slot))
    assert sch.income_slot_due(_at(8, 50), ran) is None


def test_income_slot_does_not_backfill_a_long_stale_slot():
    """Grace tolerates a missed tick; it must not fire hours late."""
    assert sch.income_slot_due(_at(13, 30), set()) is None


def test_income_slot_is_silent_on_a_weekend():
    assert sch.income_slot_due(_at(9, 0, day="2026-09-05"), set()) is None  # Saturday
```

Adjust the clock times to whatever you put in the TOML.

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_income_slot.py -q`

Expected: FAIL — `income_slot_due` does not exist.

**Step 3: Add the config**

In `config/sessions.toml`, after `[slots.action_alert]`:

```toml
[slots.income]
# The 30-45 DTE income scan (two-sided credit spreads + cash-secured puts).
# ONE pass a day by design: a 35-DTE candidate does not meaningfully re-rank
# inside fifteen minutes, and running it on the autoscan's cadence would cost
# ~690 extra /chains calls a day instead of ~23. After the open so the chain
# carries real marks rather than overnight stubs.
grace_min = 20
morning = "08:45"     # 09:45 ET
```

**Step 4: Implement the gate**

In `services/options_svc/scheduler.py`, directly beneath `action_alert_due`, add
`income_slot_due` following that function exactly — it is the house pattern for a
once-per-day slot with grace, and deviating from it buys nothing:

```python
# ── Income-window scan cadence (see config/sessions.toml [slots.income]) ─────
# ONE pass a day. Mirrors analyze_slot_due / action_alert_due: fires each slot
# once per trading day inside the grace window, never backfilling a stale one.
_INCOME_SLOTS = {k: (t.hour, t.minute) for k, t in mc.slot_times("income").items()}
_INCOME_GRACE_MIN = mc.slot_grace_min("income")


def income_slot_due(now, ran_slots):
    """Name of the income-scan slot due now, or None. Mirrors action_alert_due."""
    ...
```

**Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_income_slot.py -q`

Expected: PASS (3 tests).

**Step 6: Commit**

```bash
git add config/sessions.toml services/options_svc/scheduler.py services/options_svc/tests/test_income_slot.py
git commit -m "feat(options): add the [slots.income] once-daily scan gate"
```

---

## Task 6: Publish `cache:options:income`

**Files:**
- Modify: `services/options_svc/handlers.py`
- Test: `services/options_svc/tests/test_income_publish.py` (create)

**Step 1: Write the failing tests**

```python
def test_publish_income_writes_the_view_and_bumps_the_version(monkeypatch):
    bus = Bus(fake=True)
    # income_scan returns swing_scan's shape: {"signals", "view", "filtered_out"}.
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [{"type": "PCS"}],
                                           "view": {}, "filtered_out": 0})
    handlers.publish_income(bus, symbols=["AAPL", "MSFT"])

    env = bus.cache_get(handlers.CACHE_INCOME)
    assert env.payload["scanned_symbols"] == 2
    assert len(env.payload["candidates"]) == 2


def test_publish_income_survives_one_symbol_failing(monkeypatch):
    """A bad symbol must not lose the whole scan — and it must SAY so."""
    def _scan(sym, **kw):
        if sym == "BAD":
            raise RuntimeError("no chain")
        return {"signals": [{"type": "PCS"}], "view": {}, "filtered_out": 0}

    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "income_scan", _scan)
    handlers.publish_income(bus, symbols=["AAPL", "BAD"])

    env = bus.cache_get(handlers.CACHE_INCOME)
    assert len(env.payload["candidates"]) == 1
    assert env.payload["errors"], "a swallowed symbol failure must leave a trace"
```

Note `Bus(fake=True)` shares one `FakeServer` per test now — do not build a second
one expecting an empty cache. Call `reset_fake_bus()` if you need one.

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_income_publish.py -q`

Expected: FAIL — no `publish_income`, no `CACHE_INCOME`.

**Step 3: Implement**

Add the constants beside the other `CACHE_*` pairs:

```python
CACHE_INCOME = "cache:options:income"
EVENT_INCOME = "events:options:income"
```

Then `publish_income(bus, symbols=None)`:

- default `symbols` to the watchlist via the same call the autoscan uses;
- fetch one chain per symbol bounded to `INCOME_DTE_MIN`/`MAX`, using the existing
  concurrent fan-out helper (`services/_parallel.py:parallel_map`, pool ≤ 8);
- call `compute.income_scan` per symbol;
- wrap each symbol in `try/except` → append to `errors` **and** call
  `_degrade.degraded("options.publish_income")`. This body is well over 15 lines,
  so the house rule applies: it must speak, and
  `services/tests/test_no_silent_degrades.py` enforces it;
- rank the merged list and validate through `IncomeScan(...)` before writing;
- `bus.cache_set(CACHE_INCOME, snap.model_dump(), event=EVENT_INCOME, skip_unchanged=True)`.

Wire the call into the service loop where `action_alert_due` is consumed, gated on
`income_slot_due`. Add an `income_scan` command to `handle_command` so the page's
Refresh button can force a pass, and extend the `handle_command` docstring — it is
the dispatch's only index.

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_income_publish.py -q`

Expected: PASS (2 tests).

**Step 5: Full-service check**

Run: `.venv/bin/python -m pytest services/options_svc -q`

Expected: no NEW failures. Compare the failing **set** against your pre-task
baseline, not the count.

**Step 6: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_income_publish.py
git commit -m "feat(options): publish cache:options:income on the morning slot"
```

---

## Task 7: The `/options/income` page

**Files:**
- Create: `webgui/pages/options/income.py`
- Modify: `webgui/main.py` (`OPTIONS_CHILDREN`, plus the `@ui.page` route)
- Modify: `webgui/tests/test_shell.py`, `webgui/tests/test_no_inline_style.py`
- Test: `webgui/tests/test_income_page.py` (create)

**Step 1: Write the failing tests**

Test the **pure row builders only** — no browser:

```python
from pages.options import income


def test_rows_label_the_side_a_reader_can_act_on():
    rows = income.candidate_rows([
        {"type": "PCS", "symbol": "AAPL", "short_strike": 95.0, "credit": 1.2},
        {"type": "CCS", "symbol": "AAPL", "short_strike": 110.0, "credit": 1.1},
        {"type": "SHORT_PUT", "symbol": "MSFT", "legs": [{"strike": 400.0}], "net_credit": 500.0},
    ])
    assert [r["side"] for r in rows] == ["Put spread", "Call spread", "Cash-secured put"]


def test_a_missing_reading_renders_a_dash_never_a_zero():
    """Never print a number you did not read — an absent credit is not 0.00."""
    rows = income.candidate_rows([{"type": "PCS", "symbol": "AAPL"}])
    assert rows[0]["credit"] == "—"


def test_empty_state_distinguishes_a_cold_feed_from_a_quiet_tape():
    assert income.status_text(None) == income.WAITING          # service cold
    assert income.status_text({"candidates": []}) != income.WAITING   # ran, found none
```

`WAITING` must come from `pages/copy.py` (`WAITING_OPTIONS`), not a fresh literal —
`webgui/tests/test_shared_copy.py` reads page source and fails on a reintroduced
one.

**Step 2: Run to verify they fail**

Run: `cd webgui && ../.venv/bin/python -m pytest tests/test_income_page.py -q`

Expected: FAIL — module does not exist.

**Step 3: Implement the page**

Copy the shape of `webgui/pages/options/matrix.py`: module-level pure builders,
a thin `render()`, `bus_client` + `view_watch.watch_view("options:income", …)` for
repaints, `@guard_async` on every callback. Tier-1 rules apply — no engine import,
no `sqlite3`, no Schwab call. Tailwind tokens from `.theme` only; **no `.style()`**.

**Step 4: Register the route and the tab**

In `webgui/main.py`, insert into `OPTIONS_CHILDREN` after Strategy Finder:

```python
    ("/options/income", "Income", "savings"),
```

`savings` must not collide with an existing drawer icon — check against the
distinctness test before settling on it. Add the `@ui.page("/options/income")`
handler mirroring its neighbours, then add the route to `test_shell.py`'s expected
set and the page to `test_no_inline_style.py`.

**Step 5: Run the suite**

Run: `cd webgui && ../.venv/bin/python -m pytest -q`

Expected: no NEW failures; the shell and inline-style guards pass.

**Step 6: Commit**

```bash
git add webgui/pages/options/income.py webgui/main.py webgui/tests/
git commit -m "feat(webgui): add the Income tab reading cache:options:income"
```

---

## Task 8: Verify Phase A running in dev

Tests passing is not "verified in dev" for anything with a runtime surface.

**Step 1: Land the branch in dev**

Commit, fast-forward `Using_Highcharts`, and pull into the **dev** checkout.
Never `git pull` in prod — `.claude/hooks/guard_prod_promote.py` will block it,
and it is blocking it for a reason.

**Step 2: Drive it through Redis, not the browser**

This is the most reliable end-to-end check for a 3-tier page:

```bash
.venv/bin/python -c "from shared.bus import Bus; b=Bus(); b.enqueue_command('cmd:options', {'type':'income_scan'})"
```

Then read it back:

```bash
.venv/bin/python -c "from shared.bus import Bus; p=Bus().cache_get('cache:options:income').payload; print(p['scanned_symbols'], len(p['candidates']), p['errors'])"
```

**Step 3: Confirm both sides are actually present**

```bash
.venv/bin/python -c "from shared.bus import Bus; import collections; print(collections.Counter(c['type'] for c in Bus().cache_get('cache:options:income').payload['candidates']))"
```

Expected: `PCS`, `CCS` and `SHORT_PUT` all non-zero on a normal tape. If `CCS` is
zero, check whether `regime_filter` is blocking it — that is correct behaviour in a
committed bullish regime, not a bug. Confirm which before moving on.

**Step 4: Open the page**

Preview the **`webgui-dev` (:9500)** config. Not `webgui` — a worktree with no
`env.local.toml` resolves to PROD and binds :8500, where the live stack already is.

Check the nine-tab strip does not wrap. This is design open-risk 3 and no test
covers it.

**Step 5: Record the result**

Add a dated entry to `docs/CHANGELOG.md` with what shipped and what you observed
live, and a `/options/income` row to `docs/webgui-routes.md`.

**Step 6: Commit**

```bash
git add docs/CHANGELOG.md docs/webgui-routes.md
git commit -m "docs: record the income window verified live in dev"
```

---

# Phase B — share inventory

> Do not start Phase B until Task 8 is done and the income window is live in dev.

## Task 9: The `equity_lots` table

**Files:**
- Modify: `options-scanner/paper_account_db.py`
- Test: `options-scanner/tests/test_equity_lots.py` (create)

**Step 1: Write the failing tests**

```python
def test_insert_and_fetch_an_open_lot(tmp_path):
    db = tmp_path / "acct.db"
    pad.init_db(db)
    lot_id = pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100,
                                        "cost_basis": 95.0, "source": "assignment"})
    lots = pad.fetch_open_lots(db)
    assert [l["symbol"] for l in lots] == ["AAPL"]
    assert lots[0]["lot_id"] == lot_id


def test_equity_at_cost_sums_open_lots_only(tmp_path):
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100, "cost_basis": 95.0})
    closed = pad.insert_equity_lot(db, {"symbol": "MSFT", "shares": 100, "cost_basis": 400.0})
    pad.close_equity_lot(db, closed, exit_price=410.0, reason="called_away")
    assert pad.equity_at_cost(db) == 9500.0


def test_a_lot_does_not_disturb_reconcile_buying_power(tmp_path):
    """THE invariant. reconcile recomputes reserved from OPEN paper_positions
    alone; a lot holds cash-converted-to-shares, never a reservation. If this
    ever fails, the lot model has drifted and reconcile will silently zero
    whatever it reserved."""
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.ensure_account(db, starting_balance=25_000.0, session_date="2026-09-08")
    pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100, "cost_basis": 95.0})
    assert pad.reconcile_buying_power(db) == 0.0
```

**Step 2: Run to verify they fail**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_equity_lots.py -q -p no:randomly`

Expected: FAIL — no `insert_equity_lot`.

**Step 3: Implement**

Append to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS equity_lots (
    lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    shares INTEGER NOT NULL,
    cost_basis REAL NOT NULL,          -- per share
    opened_ts TEXT,
    source TEXT,                       -- 'assignment' | 'manual'
    source_position_id INTEGER,        -- the assigned short put, when source='assignment'
    status TEXT DEFAULT 'OPEN',
    closed_ts TEXT,
    exit_price REAL,
    realized_pnl REAL,
    exit_reason TEXT                   -- 'called_away' | 'sold'
);
CREATE INDEX IF NOT EXISTS idx_lots_status ON equity_lots(status);
CREATE INDEX IF NOT EXISTS idx_lots_symbol ON equity_lots(symbol);
```

Add `insert_equity_lot`, `fetch_open_lots`, `close_equity_lot`, `equity_at_cost`.

⚠ Give every one of them the signature shape `def f(db_path=None, ...)` and
resolve `DEFAULT_DB_PATH` **inside the body** — never `db_path=DEFAULT_DB_PATH` in
the signature. Python binds a default at `def` time, which is exactly why the old
`signal_db` monkeypatch fixture silently never worked and 24 synthetic signals
leaked into both live environments.

**Step 4: Run to verify they pass**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_equity_lots.py -q -p no:randomly`

Expected: PASS (3 tests).

**Step 5: Commit**

```bash
git add options-scanner/paper_account_db.py options-scanner/tests/test_equity_lots.py
git commit -m "feat(paper): add the equity_lots table and equity_at_cost"
```

---

## Task 10: Shares count toward session-start equity

**Files:**
- Modify: `options-scanner/paper_account_db.py` (`roll_session_if_needed`)
- Test: `options-scanner/tests/test_equity_lots.py`

**Step 1: Write the failing test**

```python
def test_session_start_equity_includes_shares_at_cost(tmp_path):
    """Shares held at cost basis are committed capital by the same definition
    that puts buying_power_reserved in this sum. Omitting them understates
    equity for any session opening with stock, loosening the drawdown guard."""
    db = tmp_path / "acct.db"
    pad.init_db(db)
    pad.ensure_account(db, starting_balance=25_000.0, session_date="2026-09-07")
    pad.insert_equity_lot(db, {"symbol": "AAPL", "shares": 100, "cost_basis": 95.0})

    pad.roll_session_if_needed(db, "2026-09-08")

    assert pad.get_account(db)["session_start_equity"] == 25_000.0 + 9_500.0
```

**Step 2: Run to verify it fails**

Expected: FAIL — equity reads 25000.0.

**Step 3: Implement**

In `roll_session_if_needed`, extend the equity line and the comment above it:

```python
            # Committed capital at roll. Open unrealized P&L is deliberately
            # excluded (should_halt tracks realized + unrealized separately);
            # shares are included AT COST for exactly that reason — a lot's cost
            # basis is committed capital, its mark is unrealized.
            equity = (a["cash"] + a["buying_power_reserved"]
                      + _equity_at_cost_conn(conn))
```

Add `_equity_at_cost_conn(conn)` so this reuses the open connection inside the
existing transaction rather than opening a second one mid-`with`.

**Step 4: Run to verify it passes**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_equity_lots.py -q -p no:randomly`

Expected: PASS (4 tests).

**Step 5: Commit**

```bash
git add options-scanner/paper_account_db.py options-scanner/tests/test_equity_lots.py
git commit -m "feat(paper): count shares at cost toward session-start equity"
```

---

## Task 11: Assignment on settlement

**Files:**
- Modify: `options-scanner/paper_engine.py` (the settlement branch)
- Test: `options-scanner/tests/test_assignment.py` (create)

**Step 1: Write the failing tests**

```python
def test_an_itm_short_put_becomes_shares_at_the_strike(tmp_path, fake_client):
    """The wheel's whole loop. Premium is kept; cash buys the stock at the
    strike; the lot's basis is the strike, NOT the settlement price."""
    db = _account_with_open_short_put(tmp_path, strike=100.0, credit=2.0, qty=1)
    fake_client.set_quote("AAPL", 92.0)          # ITM at expiry

    paper_engine.manage_positions(db_path=db, client=fake_client,
                                  now_ct=_expiry_close_ct())

    lots = pad.fetch_open_lots(db)
    assert len(lots) == 1
    assert lots[0]["shares"] == 100
    assert lots[0]["cost_basis"] == 100.0
    assert lots[0]["source"] == "assignment"


def test_assignment_leaves_buying_power_reconciled(tmp_path, fake_client):
    """The invariant, driven end-to-end from the PRODUCER rather than asserted
    on a hand-built payload — a consumer-side guard proves nothing until a test
    drives it from the thing that actually writes."""
    db = _account_with_open_short_put(tmp_path, strike=100.0, credit=2.0, qty=1)
    fake_client.set_quote("AAPL", 92.0)
    paper_engine.manage_positions(db_path=db, client=fake_client,
                                  now_ct=_expiry_close_ct())
    assert pad.reconcile_buying_power(db) == 0.0


def test_an_otm_short_put_expires_worthless_and_makes_no_lot(tmp_path, fake_client):
    db = _account_with_open_short_put(tmp_path, strike=100.0, credit=2.0, qty=1)
    fake_client.set_quote("AAPL", 108.0)
    paper_engine.manage_positions(db_path=db, client=fake_client,
                                  now_ct=_expiry_close_ct())
    assert pad.fetch_open_lots(db) == []
```

**Step 2: Run to verify they fail**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_assignment.py -q -p no:randomly`

Expected: FAIL — no lot is created.

**Step 3: Implement**

In the settlement branch of `manage_positions`, immediately after the existing
`_close(...)` call, add the assignment step for a cash-secured short put that
settled ITM. Three moves, in this order:

1. `_close(..., reason="ASSIGNED", status="EXPIRED")` — the existing call, with
   the reason changed for this case only;
2. the existing `release_buying_power` path returns the strike-notional
   reservation to cash;
3. debit cash `strike × 100 × qty` and `insert_equity_lot(..., cost_basis=strike,
   source="assignment", source_position_id=pos["position_id"])`.

Do **not** add a second detection path. The branch already defers a cycle when no
underlying quote is available; an assignment that defers settles next cycle. Two
mechanisms that can disagree is worse than one that is occasionally a cycle late.

**Step 4: Run to verify they pass**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests/test_assignment.py -q -p no:randomly`

Expected: PASS (3 tests).

**Step 5: Full-suite check**

Run: `cd options-scanner && ../.venv/bin/python -m pytest tests -q -p no:randomly`

Expected: no NEW failures against your baseline set.

**Step 6: Commit**

```bash
git add options-scanner/paper_engine.py options-scanner/tests/test_assignment.py
git commit -m "feat(paper): assign an ITM short put into an equity lot at expiry"
```

---

## Task 12: Covered-call candidates

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_covered_calls.py` (create)

**Step 1: Write the failing tests**

```python
def test_never_offers_a_strike_below_cost_basis(fake_chain_30_45):
    """A hard floor, not a warning: a call below basis books a guaranteed loss
    on the shares if called away."""
    lots = [{"symbol": "AAPL", "shares": 100, "cost_basis": 105.0}]
    out = compute.covered_call_candidates(lots, chains={"AAPL": fake_chain_30_45},
                                          spots={"AAPL": 100.0})
    assert out, "a basis above spot still has strikes above it further out"
    assert all(c["short_strike"] >= 105.0 for c in out)


def test_reports_yield_on_cost_and_total_return_if_called(fake_chain_30_45):
    lots = [{"symbol": "AAPL", "shares": 100, "cost_basis": 95.0}]
    c = compute.covered_call_candidates(lots, chains={"AAPL": fake_chain_30_45},
                                        spots={"AAPL": 100.0})[0]
    assert c["yield_on_cost"] == pytest.approx(c["credit"] * 100 / (95.0 * 100))
    expected = ((c["short_strike"] - 95.0) + c["credit"]) * 100 / (95.0 * 100)
    assert c["total_return_if_called"] == pytest.approx(expected)


def test_sizes_to_whole_lots_only(fake_chain_30_45):
    """149 shares covers one contract, not 1.49."""
    lots = [{"symbol": "AAPL", "shares": 149, "cost_basis": 95.0}]
    out = compute.covered_call_candidates(lots, chains={"AAPL": fake_chain_30_45},
                                          spots={"AAPL": 100.0})
    assert all(c["quantity"] == 1 for c in out)


def test_no_lots_means_no_candidates():
    assert compute.covered_call_candidates([], chains={}, spots={}) == []
```

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_covered_calls.py -q`

Expected: FAIL — no `covered_call_candidates`.

**Step 3: Implement**

`covered_call_candidates(lots, chains, spots, earnings=None)` — for each open lot,
screen calls in the `INCOME` DTE window with `strike >= cost_basis`, size
`quantity = shares // 100`, and emit the normalised single-leg shape plus
`yield_on_cost`, `total_return_if_called` and `covered=True`. Apply the same
earnings gate. Skip a lot with fewer than 100 shares entirely.

Then fold the result into `publish_income` so covered calls land in the same
ranked list.

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_covered_calls.py -q`

Expected: PASS (4 tests).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_covered_calls.py
git commit -m "feat(options): covered-call candidates struck at or above cost basis"
```

---

## Task 13: The `/options/shares` page

**Files:**
- Create: `webgui/pages/options/shares.py`
- Modify: `webgui/main.py`, `webgui/tests/test_shell.py`, `webgui/tests/test_no_inline_style.py`
- Test: `webgui/tests/test_shares_page.py` (create)

Follow Task 7 exactly — pure row builders tested without a browser, thin
`render()`, Tier-1 imports only, Tailwind tokens, shared copy for the empty state.

The tab goes into `OPTIONS_CHILDREN` **after Paper Account**, in the *track* phase:

```python
    ("/options/shares", "Shares", "inventory_2"),
```

Rows show symbol, shares, cost basis, live mark, unrealized, and the covering call
if one is open. One column needs care: label the assignment source so a lot's
provenance is visible — a lot that appeared from an assignment and one entered by
hand are not the same fact.

Publish the lots into the existing paper-account view rather than inventing a new
one; the page is a second reader of the book, not a second book.

**Commit**

```bash
git add webgui/pages/options/shares.py webgui/main.py webgui/tests/
git commit -m "feat(webgui): add the Shares tab over the paper account's equity lots"
```

---

## Task 14: Verify Phase B in dev, then document

**Step 1: Drive an assignment end-to-end in dev**

Open a short put in the manual paper book at a strike above spot, then run the
manage cycle at/after 15:00 CT on its expiry day. Confirm via Redis that a lot
appeared, and that `reconcile_buying_power` returns `0.0`.

**Step 2: Confirm the covered call appears**

Force an income scan and check the candidate list now contains a `covered=True`
row for the assigned symbol, struck at or above basis.

**Step 3: Update the docs that rot**

- `docs/CHANGELOG.md` — dated entry, both phases, what you verified live.
- `docs/webgui-routes.md` — `/options/income` and `/options/shares` rows.
- `webgui/page_help.py` — hover guides for both pages. **This is the fifth manual
  and the most-read prose in the app**; it is also the thing most likely to be
  skipped here.
- `docs/manuals/` — the User Guide gains the income + assignment workflow; the
  Technical Reference gains the `[slots.income]` cadence and the equity-at-cost
  rule. A user-visible behaviour change lands in the manuals, not only the
  CHANGELOG.

**Step 4: Update `CLAUDE.md` — but only the invariants**

A shipped feature is not an entry there. What *is*: the new route rows in the
table, and the equity-lots buying-power rule, which is a genuine trap for the next
person. Everything else belongs in the CHANGELOG. Correct in place; never append.

**Step 5: Commit**

```bash
git add docs/ webgui/page_help.py CLAUDE.md
git commit -m "docs: income window + share inventory, verified live in dev"
```

---

## Definition of done

- [ ] Every suite touched shows no NEW failures — compared by **node-id set**, and by the **skipped** set too, since which test self-skips varies run to run.
- [ ] `.venv/bin/python -m pyright` is clean (it covers `shared/contracts` and `shared/bus`, both touched here).
- [ ] The income view carries `PCS`, `CCS` and `SHORT_PUT` rows live in dev.
- [ ] `reconcile_buying_power` returns `0.0` after a real assignment in dev.
- [ ] The nine-tab strip does not wrap at the narrowest width you can produce.
- [ ] Manuals, `page_help.py`, routes doc and CHANGELOG all updated.
- [ ] Nothing promoted to prod. Promotion is `tools/promote.sh`, run separately, after you have watched this work in dev.
