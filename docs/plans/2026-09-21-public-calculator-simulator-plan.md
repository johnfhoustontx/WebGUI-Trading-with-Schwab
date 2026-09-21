# Public Calculator + Simulator Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish the Calculator and a Price-&-Time-only Simulator on
`live.neuralstrike.co` (`/calculator`, `/simulator`), answered by a public
tools worker in options_svc, with the Calculator's position handed to the
Simulator inside the visitor's own browser tab.

**Architecture:** Two new public streams (`cmd:tools_public` for requests that
spend Schwab calls, `cmd:tools_public_math` for pure pricing), each on its own
consumer loop. One per-symbol public chain key shared with the Rescue form, the
full quoted chain held only in the worker's memory. One daily Schwab budget
across Rescue, Calculator and Simulator. Pages built from the private pages'
own module-level pieces. Design:
[`2026-09-21-public-calculator-simulator-design.md`](2026-09-21-public-calculator-simulator-design.md).

**Tech Stack:** Python 3.11, NiceGUI 3.x (Tier 1), FastAPI services
(`services/_scaffold.make_app`), Redis via `shared/bus` (fakeredis under
pytest), pytest.

---

## Read before starting

1. **CLAUDE.md**, sections *The public live screens — a SECOND Tier-1
   process*, *3-tier architecture* (the Tier-1 import allow-list), *A NaN
   clamps to the HIGH bound*, and *Observability*.
2. **The precedent you are copying**, built earlier the same day. Read all of:
   `shared/public_rescue.py`, `services/options_svc/rescue_public.py`,
   `webgui/pages/options/rescue_live.py`, and their tests
   `shared/tests/test_public_rescue.py`,
   `services/options_svc/tests/test_rescue_public.py`,
   `webgui/tests/test_rescue_live.py`. Their structure (validator → command
   builder → per-request answer key → worker refusals before any Schwab call →
   page polling its own answer) is the structure here.
3. **The review of that precedent** — the CHANGELOG entry "Independent review,
   same day". Every guard it forced is required here from the start.

## Rules for whoever executes this

- **TDD.** Test first, watch it fail, then code. For every refusal, prove
  with a mutation (delete the guard, watch the test fail, restore).
- **Never weaken a test to make it pass.** If a pre-existing guard test fails
  (`test_live_commands.py`, `test_bus_client.py`'s write-function count,
  `test_finder_public.py`'s consumer list, the site tests), that test is
  telling you to make a deliberate, visible change to it — update its expected
  value and say why in a comment. Do not narrow what it checks.
- **Run tests per suite** (CLAUDE.md "Tests"). From the repo root, with the
  checkout's venv (`../../../.venv/Scripts/python` in a Windows worktree):
  - `python -m pytest shared/tests -q`
  - `python -m pytest services/options_svc -q`
  - `python -m pytest services/tests deploy tools/tests tests -q`
  - `(cd webgui && python -m pytest -q -p no:randomly)`
  - `python -m pyright` must report 0 errors.
- **Commit after every task.** End each commit message with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## Phase A — the shared request module

### Task 1: `shared/public_tools.py` — streams, keys, config

**Files:**
- Create: `shared/public_tools.py`
- Create: `config/tools_public.toml`
- Modify: `repo_paths.py` (after `RESCUE_PUBLIC_TOML`)
- Test: `shared/tests/test_public_tools.py`

**Step 1: Write the failing test** (`shared/tests/test_public_tools.py`):

```python
"""shared.public_tools: the public Calculator/Simulator requests."""
import datetime as dt
import pathlib
import subprocess
import sys

import pytest

from shared import public_tools as pt

REPO = pathlib.Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 9, 21)
EXP = "2026-10-16"


def test_two_streams_neither_the_owners_nor_rescues():
    from shared import public_rescue, public_scan
    assert pt.TOOLS_STREAM == "cmd:tools_public"
    assert pt.MATH_STREAM == "cmd:tools_public_math"
    assert len({pt.TOOLS_STREAM, pt.MATH_STREAM, public_rescue.STREAM,
                public_scan.STREAM, "cmd:options"}) == 5


def test_every_request_kind_is_on_exactly_one_stream():
    assert set(pt.TOOLS_KINDS) & set(pt.MATH_KINDS) == set()
    assert set(pt.TOOLS_KINDS) == {"chain", "expiry", "rate", "sim_snapshot",
                                   "sim_expiry"}
    assert set(pt.MATH_KINDS) == {"price", "iv", "sweep"}


def test_the_shipped_file_matches_the_defaults():
    import tomllib
    shipped = tomllib.loads((REPO / "config" / "tools_public.toml")
                            .read_text(encoding="utf-8"))
    for section, values in pt.DEFAULTS.items():
        assert shipped[section] == values, section


EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.public_rescue",
            "shared.public_tools", "shared.symbols", "tzdata"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.public_tools
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names)))
""" % REPO


def test_public_tools_imports_only_config_and_validators():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
```

**Step 2: Run it** — `python -m pytest shared/tests/test_public_tools.py -q`.
Expected: FAIL, `ModuleNotFoundError: shared.public_tools`.

**Step 3: Implement.** Model the module on `shared/public_rescue.py` (read it
first). Contents:

```python
"""The public Calculator and Simulator: their requests, keys and config.

[Module docstring in the style of shared/public_rescue.py: what the two
streams carry and why they are two; that every field is normalized here
before anything is written and again in the worker; that STREAM names are in
the live ACL user's write selectors (runbook section 2 steps 4e/4f); Tier-1
allow-listed, imports pinned by shared/tests/test_public_tools.py.]
"""
import hashlib
import json

from repo_paths import TOOLS_PUBLIC_TOML
from shared import public_rescue as _pr          # clean_expiry, _finite, _qty
from shared.config_toml import toml_loader
from shared.symbols import clean_symbol

TOOLS_STREAM = "cmd:tools_public"      # spends Schwab calls
MATH_STREAM = "cmd:tools_public_math"  # pure pricing, never a Schwab call
TOOLS_TYPE = "public_tool"
MATH_TYPE = "public_math"
TOOLS_KINDS = ("chain", "expiry", "rate", "sim_snapshot", "sim_expiry")
MATH_KINDS = ("price", "iv", "sweep")

STATUS_VIEW = "options:tools_public_status"
STATUS_KEY = f"cache:{STATUS_VIEW}"


def chain_view(symbol) -> str:
    """The ONE public chain key per symbol, shared with the Rescue form."""
    return _pr.ladder_view(symbol)          # see Task 3: now options:pub_chain:<SYM>


def result_view(key) -> str:
    return f"options:tools_pub:{key}"


def answer_view(key) -> str:
    return f"options:tools_pub_answer:{key}"


def cache_key(view) -> str:
    return f"cache:{view}"


def event(view) -> str:
    return f"events:{view}"


DEFAULTS = {
    "limits": {
        "result_ttl_min": 5,        # a price/sweep result reused for the same request
        "rate_ttl_min": 15,         # a Rate My Trade result reused
        "dedup_sec": 60,
        "structure_runs": 3,        # Rate My Trade runs per structure per rate_ttl_min
        "max_wait_sec": 120,
        "result_keep_min": 30,
        "answer_keep_min": 15,
        "snapshot_limit": 8,        # public Simulator snapshots held
        "snapshot_ttl_min": 15,
        "chain_hold_limit": 16,     # quoted chains held in worker memory
    },
    "visitor": {
        "tools_per_hour": 60,       # chain / expiry / rate / snapshot requests
        "math_per_hour": 600,       # price / iv / sweep requests
    },
}

load, reset_cache = toml_loader(TOOLS_PUBLIC_TOML, DEFAULTS,
                                label="tools_public.toml")
```

Add `limits()`, `tools_per_hour()`, `math_per_hour()` exactly as
`public_rescue` does (`_num` with a minimum, bool refused). **The daily Schwab
budget is NOT here** — it is shared with Rescue and lives in Task 4.

`config/tools_public.toml`: every key of `DEFAULTS`, each with a one-line
plain-English comment (copy the tone of `config/rescue_public.toml`).
`repo_paths.py`: `TOOLS_PUBLIC_TOML = REPO_ROOT / "config" / "tools_public.toml"`
with a three-line comment like its neighbours.

**Step 4: Run the test.** Expected: PASS.

**Step 5: Commit** — `feat(public tools): shared module, streams and config`.

---

### Task 2: request validators and command builders

**Files:**
- Modify: `shared/public_tools.py`
- Test: `shared/tests/test_public_tools.py`

Every builder returns `{"type": TOOLS_TYPE | MATH_TYPE, "args": {"kind": ...,
...}}` or `None`. Unknown keys are dropped. Any present-but-unusable field
refuses the whole request.

**Step 1: Write the failing tests.** Add to the test file, one test per rule:

```python
def _legs(**over):
    leg = {"option_type": "put", "side": "short", "strike": 500.0,
           "expiry": EXP, "qty": 1, "premium": 1.2}
    leg.update(over)
    return [leg, {**leg, "side": "long", "strike": 495.0, "premium": 0.5}]


def _price(**over):
    req = {"kind": "price", "symbol": "spy", "strategy": "PCS", "spot": 502.0,
           "iv": 0.18, "rate": 0.045, "ivadj": 0.0, "qty": 1, "expiry": EXP,
           "legs": _legs(), "num_strikes": 24}
    req.update(over)
    return req


def test_a_price_request_normalizes_to_calc_compute_arguments():
    cmd = pt.math_command(_price(junk=1), TODAY)
    assert cmd["type"] == pt.MATH_TYPE
    args = cmd["args"]
    assert args["kind"] == "price" and args["symbol"] == "SPY"
    assert set(args) == {"kind", "symbol", "strategy", "spot", "iv", "rate",
                         "ivadj", "qty", "expiry", "legs", "num_strikes"}


@pytest.mark.parametrize("field, bad", [
    ("spot", float("nan")), ("spot", 0), ("spot", True),
    ("iv", float("inf")), ("iv", 0), ("iv", 5.01),        # 0 < iv <= 5.0 (500%)
    ("rate", -0.01), ("rate", 0.21), ("ivadj", 2.0),
    ("qty", 0), ("qty", 101), ("num_strikes", 4), ("num_strikes", 61),
    ("expiry", "2026-09-01"), ("strategy", "x" * 40),
])
def test_an_unusable_price_field_refuses_the_request(field, bad):
    assert pt.math_command(_price(**{field: bad}), TODAY) is None


@pytest.mark.parametrize("leg", [
    {"strike": float("nan")}, {"premium": float("inf")}, {"premium": True},
    {"option_type": "future"}, {"side": "both"}, {"qty": 0},
    {"expiry": "yesterday"},
])
def test_an_unusable_leg_refuses_the_request(leg):
    assert pt.math_command(_price(legs=_legs(**leg)), TODAY) is None


def test_a_share_leg_carries_no_strike_or_expiry():
    legs = [{"option_type": "stock", "side": "long", "qty": 1, "premium": 500.0},
            {"option_type": "call", "side": "short", "strike": 510.0,
             "expiry": EXP, "qty": 1, "premium": 2.0}]
    args = pt.math_command(_price(strategy="COVERED_CALL", legs=legs), TODAY)["args"]
    assert args["legs"][0] == {"option_type": "stock", "side": "long", "qty": 1,
                               "premium": 500.0, "strike": None, "expiry": None}


def test_too_many_legs_refuses_the_request():
    assert pt.math_command(_price(legs=_legs() * 5), TODAY) is None   # max 8


def test_iv_and_sweep_requests():
    iv = pt.math_command({"kind": "iv", "symbol": "SPY", "expiry": EXP,
                          "strike": 500.0, "option_type": "put"}, TODAY)
    assert iv["args"] == {"kind": "iv", "symbol": "SPY", "expiry": EXP,
                          "strike": 500.0, "option_type": "put"}
    sweep = pt.math_command({"kind": "sweep", "symbol": "SPY", "dt": 5.0,
                             "legs": [{"kind": "put", "strike": 500.0,
                                       "expiry": EXP, "side": "short", "qty": 1}]},
                            TODAY)
    assert sweep["args"]["dt"] == 5.0
    assert pt.math_command({"kind": "sweep", "symbol": "SPY", "dt": -1,
                            "legs": sweep["args"]["legs"]}, TODAY) is None
    assert pt.math_command({"kind": "sweep", "symbol": "SPY", "dt": 400,
                            "legs": sweep["args"]["legs"]}, TODAY) is None


def test_tools_requests():
    assert pt.tools_command({"kind": "chain", "symbol": " spy "})["args"] == \
        {"kind": "chain", "symbol": "SPY"}
    assert pt.tools_command({"kind": "expiry", "symbol": "SPY", "expiry": EXP},
                            TODAY)["args"]["expiry"] == EXP
    assert pt.tools_command({"kind": "sim_snapshot", "symbol": "SPY"}) is not None
    rate = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCS",
                             "legs": _legs()}, TODAY)
    assert rate["args"]["structure"] == "PCS"
    for bad in ({"kind": "price", "symbol": "SPY"},        # a math kind on tools
                {"kind": "chain", "symbol": "../x"},
                {"kind": "expiry", "symbol": "SPY", "expiry": "soon"},
                {"kind": "nope"}, "x", None):
        assert pt.tools_command(bad, TODAY) is None


def test_a_math_kind_is_refused_by_the_tools_builder_and_vice_versa():
    assert pt.math_command({"kind": "chain", "symbol": "SPY"}) is None


def test_request_keys_are_content_addressed_and_say_nothing():
    a = pt.request_key(pt.math_command(_price(), TODAY))
    b = pt.request_key(pt.math_command(_price(symbol=" SPY ", junk=2), TODAY))
    assert a == b and pt.is_key(a) and "SPY" not in a.upper()
    assert a != pt.request_key(pt.math_command(_price(iv=0.2), TODAY))


def test_rate_structure_key_ignores_price_and_size():
    one = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCS",
                            "legs": _legs()}, TODAY)
    two = pt.tools_command({"kind": "rate", "symbol": "SPY", "structure": "PCS",
                            "legs": _legs(premium=3.0, qty=4)}, TODAY)
    assert pt.structure_key(one["args"]) == pt.structure_key(two["args"])
```

**Step 2: Run.** Expected: FAIL (`math_command` undefined).

**Step 3: Implement** in `shared/public_tools.py`, reusing
`public_rescue._finite`, `_qty` and `clean_expiry` (import them; do not copy):

- `_clean_calc_leg(raw, today)`: `option_type` in `{"call","put","stock"}`;
  `side` in `{"long","short"}`; `qty` via `_qty`; `premium` via `_finite`
  with `abs <= 100_000` (None → refuse; a typed price is required, 0.0 is
  allowed); for options `strike` via `_finite(lo=0, hi=1e6)` and `expiry` via
  `clean_expiry`; for stock both forced to `None`.
- `_clean_sim_leg(raw, today)`: keys `kind` (call/put), `strike`, `expiry`,
  `side`, `qty` — the Simulator's leg shape (`simulator._legs_payload`).
- `math_command(raw, today=None)`:
  - `price`: symbol, `strategy` (str, `len <= 32`, `[A-Z_]` only after
    upper-casing), `spot` (0, 1e6], `iv` (0, 5.0], `rate` [0, 0.20],
    `ivadj` [-1.0, 1.0], `qty` 1–100, `expiry`, `legs` 1–8 calc legs,
    `num_strikes` 5–60. **No `price_rows`** — the worker derives them from
    the held chain (so a visitor cannot send 10,000 rows).
  - `iv`: symbol, expiry, strike, option_type call/put. **No mark** — the
    worker reads it from the held chain.
  - `sweep`: symbol, `dt` [0, 366], legs 1–8 sim legs.
- `tools_command(raw, today=None)`:
  - `chain`: symbol. `expiry` / `sim_expiry`: symbol + expiry.
    `sim_snapshot`: symbol.
  - `rate`: symbol, `structure` (as `strategy` above), legs 1–8 calc legs.
- `request_key(command)`: `sha256(json.dumps(["tools"|"math", args],
  sort_keys=True))[:24]`, same as `public_rescue._hash`.
- `structure_key(args)`: the same hash over `args` with every leg's
  `premium` and `qty` removed.
- `is_key(raw)`: as `public_rescue.is_key`.

**Step 4: Run.** Expected: PASS. Then mutation-check one guard: replace the
`math.isfinite` check in `public_rescue._finite` with `if False:` and confirm
at least the NaN/inf cases here fail; restore.

**Step 5: Commit** — `feat(public tools): request validators and builders`.

---

### Task 3: one public chain key, shared with Rescue

**Files:**
- Modify: `shared/public_rescue.py` (`ladder_view`)
- Modify: `services/options_svc/tests/test_rescue_public.py`,
  `webgui/tests/test_rescue_live.py`, `shared/tests/test_public_rescue.py`
  (only where they spell the old key string)

**Step 1:** Change the test in `shared/tests/test_public_rescue.py`:

```python
assert pr.cache_key(pr.ladder_view("spy")) == "cache:options:pub_chain:SPY"
```

**Step 2:** Run it — FAIL (still `rescue_pub_ladder`).

**Step 3:** In `shared/public_rescue.py` change `ladder_view` to return
`f"options:pub_chain:{SYMBOL}"`, and update its docstring: the one public
chain key per symbol, shared by Rescue, Calculator and Simulator; carries
quotes only while `public_scan.show_leg_quotes()` is on (Task 5).

**Step 4:** Run all three Rescue test files — PASS (they call `ladder_view`,
not the literal; fix any literal you find).

**Step 5: Commit** — `refactor(public): Rescue's strikes list becomes the shared pub_chain key`.

⚠ This renames a key the Rescue page reads. It is safe only because Rescue is
not promoted; Tasks 1–16 ship in ONE promote.

---

## Phase B — the service side

### Task 4: one daily Schwab budget across the public workers

**Files:**
- Create: `services/options_svc/public_budget.py`
- Modify: `services/options_svc/rescue_public.py`
- Modify: `shared/public_rescue.py` DEFAULTS, `config/rescue_public.toml`,
  `webgui/config_schema.py`
- Test: `services/options_svc/tests/test_public_budget.py`

All three public workers are threads in ONE process (options_svc), so a
module-level `threading.Lock` around a read-modify-write of one status key is
enough; no Redis atomics are needed.

**Step 1: Failing test:**

```python
"""One daily Schwab budget, shared by every public worker thread."""
import datetime as dt
import threading
from zoneinfo import ZoneInfo

from services.options_svc import public_budget as pb
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 21, 10, 0, tzinfo=CT)


def _bus():
    reset_fake_bus()
    return Bus(fake=True)


def test_spend_stops_at_the_limit_and_counts_by_kind():
    bus = _bus()
    assert [pb.spend(bus, "rescue_compute", 3, NOW) for _ in range(4)] == \
        [True, True, True, False]
    st = pb.status(bus, NOW)
    assert st["spent"] == 3 and st["by_kind"] == {"rescue_compute": 3}


def test_the_budget_resets_on_a_new_ct_day():
    bus = _bus()
    pb.spend(bus, "chain", 1, NOW)
    assert pb.spend(bus, "chain", 1, NOW + dt.timedelta(days=1)) is True


def test_concurrent_threads_never_overspend():
    bus = _bus()
    wins = []
    def go():
        wins.append(pb.spend(bus, "chain", 50, NOW))
    ts = [threading.Thread(target=go) for _ in range(200)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(wins) == 50
```

**Step 2:** Run — FAIL.

**Step 3:** Implement `public_budget.py`: `BUDGET_KEY =
"cache:options:public_budget"`; `_LOCK = threading.Lock()`;
`spend(bus, kind, limit, now) -> bool` (inside the lock: read, roll over on a
new CT date, refuse at `spent >= limit`, else increment `spent` and
`by_kind[kind]`, write); `status(bus, now) -> dict`. Docstring explains why one
budget and why a lock suffices.

Then the ONE limit: add `"daily_budget": 600` under a new `[budget]` section
of `shared/public_rescue.DEFAULTS` + `config/rescue_public.toml` + a catalogue
field, **and delete** `limits.daily_budget` / `limits.ladder_budget` there.
In `rescue_public.py` replace the two budget checks with
`public_budget.spend(bus, "rescue_compute" | "chain", pr.budget(), now)`,
called at the point the Schwab work starts (a refusal returns `"budget"`).
Keep `computes_today` / `ladders_today` in its status for Settings.

**Step 4:** Run `services/options_svc/tests/test_public_budget.py` and
`test_rescue_public.py` (update its two budget tests to monkeypatch
`pr.budget` — say in a comment that the budget is now shared). PASS.

**Step 5: Commit** — `feat(public): one daily Schwab budget shared by the public workers`.

---

### Task 5: `public_chain.py` — held quoted chains, published stripped

**Files:**
- Create: `services/options_svc/public_chain.py`
- Modify: `services/options_svc/rescue_public.py` (move `strikes_from_chain`,
  `_load_ladder` and `_spot` out; call the new module)
- Test: `services/options_svc/tests/test_public_chain.py`

The Calculator's Rate My Trade (`rate_trade.rate(..., chain_payload)`) and
implied volatility (`compute.calc_iv(..., mark)`) need the **quoted** chain.
Redis gets it only when the quotes switch is on. So the worker holds the full
thin chain in memory and publishes a stripped copy.

**Step 1: Failing tests** (reuse the fake-Schwab fixture shape from
`test_rescue_public.py`):

- `test_a_load_holds_the_quoted_chain_and_publishes_strikes_only` — with
  `public_scan.show_leg_quotes` patched False, the published `pub_chain`
  contains no `bid`/`ask`/`mark`/`delta`, and `public_chain.held("SPY")`
  returns the thin chain with them.
- `test_with_quotes_on_the_published_chain_carries_quotes` — patched True:
  `payload["quotes"][NEAR]["put"]["500.0"] == {"bid":..,"ask":..,"mark":..,
  "delta":..}` and nothing else per strike.
- `test_held_chains_are_bounded_and_expire` — load 17 symbols with
  `chain_hold_limit=16`: the first is evicted; advance the clock past
  `ladder_ttl_min`: `held()` returns None.
- `test_an_expiry_merge_updates_the_held_chain_too`.
- `test_a_failed_fetch_holds_nothing_and_publishes_nothing` (the Rescue review
  rule).

**Step 2:** Run — FAIL.

**Step 3:** Implement:
- `_HELD: OrderedDict[symbol -> (loaded_mono, thin_chain_payload)]`, a lock,
  `hold()`, `held(symbol)` (None when absent or older than
  `public_rescue.limits()["ladder_ttl_min"]`), `reset()`.
- `load(bus, symbol, expiry, existing, now, *, fresh) -> (payload|None, outcome)`
  — the body of today's `rescue_public._load_ladder`, plus: hold the full
  `calc_load_symbol` result (or the merged one) in `_HELD`; build the
  published payload with `strikes_from_chain`, and add `"quotes"` from the
  thin chain **only if** `public_scan.show_leg_quotes()`.
- `publish(bus, symbol, payload, keep_min)`.

`rescue_public._handle_ladder` then calls `public_chain.load` / `publish`.
All of `test_rescue_public.py` must stay green unchanged except imports.

**Step 4:** Run both test files — PASS.

**Step 5: Commit** — `feat(public): hold quoted chains in the worker, publish them stripped`.

---

### Task 6: a separate store for public Simulator snapshots

**Files:**
- Modify: `services/options_svc/compute.py` (`_stash_sim_snapshot`,
  `sim_fetch`, `sim_fetch_expiry`, `sim_run`, around lines 8494–8780)
- Test: `services/options_svc/tests/test_sim_public_store.py`

**Step 1: Failing test:**

```python
def test_a_public_snapshot_never_touches_the_owners(monkeypatch):
    from services.options_svc import compute
    compute._SIM_SNAPSHOTS.clear()
    owner = object()
    compute._SIM_SNAPSHOTS["SPY"] = owner
    store = compute.SimStore(limit=2, ttl_sec=900)
    fake = type("Snap", (), {"symbol": "SPY", "contracts": [], "spot": 500.0})()
    monkeypatch.setattr(compute, "_fetch_sim_snapshot",
                        lambda symbol, lazy, expiries: (fake, [], {}))
    compute.sim_fetch("SPY", lazy=True, store=store)
    assert compute._SIM_SNAPSHOTS["SPY"] is owner
    assert store.get("SPY") is fake
```

plus `test_the_public_store_is_bounded_and_expires` (limit 2, three symbols,
clock advanced past `ttl_sec`) and
`test_sim_run_with_no_snapshot_in_the_store_returns_empty`.

**Step 2:** Run — FAIL.

**Step 3:** Introduce `class SimStore` (`snapshots`, `expirations`, `limit`,
`ttl_sec`, `get`, `put`, `expirations_of`, `clear`) and a module instance
`PRIVATE_SIM = SimStore(...)` **wrapping the existing `_SIM_SNAPSHOTS` /
`_SIM_EXPIRATIONS` dicts** (same objects, so every existing test and caller is
unchanged, `ttl_sec=None` meaning no expiry). Add `store=None` (→
`PRIVATE_SIM`) to `sim_fetch`, `sim_fetch_expiry`, `sim_run`, and route every
read and write through it. Extract the fetch loop into
`_fetch_sim_snapshot(symbol, lazy, expiries)` so the test can stub it.

**Step 4:** Run the new test and the whole Simulator suite
(`python -m pytest services/options_svc -q -k sim`). PASS, unchanged counts.

**Step 5: Commit** — `refactor(simulator): snapshots through a store, so the public side can have its own`.

---

### Task 7: the tools worker — `services/options_svc/tools_public.py`

**Files:**
- Create: `services/options_svc/tools_public.py`
- Modify: `services/options_svc/app.py`
- Modify: `services/options_svc/tests/test_finder_public.py` (the consumer-list pin)
- Test: `services/options_svc/tests/test_tools_public.py`

Two handlers, one per stream: `handle_tools(bus, command)` and
`handle_math(bus, command)`. Both **never raise** (copy the
`rescue_public.handle` / `_dispatch` split, including the second `try` around
the status clear).

**Answer and result keys:** `public_tools.answer_view(request_key)` and
`result_view(request_key)`, written exactly like `rescue_public._answer`.

**Tools requests** (refusal order, all before any Schwab call: `invalid` →
`expired` → `cached` → `not_listed` / `no_options` from a fresh held chain →
`duplicate` → `throttled` (rate only, per `structure_key`) → `closed`
(`[windows.tools_public]`) → `budget` via `public_budget.spend`):
- `chain` / `expiry` → `public_chain.load` + `publish` (the same code Rescue
  uses; the answer key is `public_tools.request_key`, not Rescue's).
- `rate` → requires a held chain (else `load_first`); `rate_trade.rate(symbol,
  structure, legs, held_chain, market_state=handlers._market_state(bus))`;
  **drop the checklist's Paper book line** from `out["row"]` before storing
  (find it by the key `detail.checklist_candidate` renders it under — read
  `webgui/pages/options/detail.py` and `options_svc/compute.py`'s checklist
  builder to name it exactly, and pin the removal with a test); an
  `out["error"]` is answered `"error"` and never cached.
- `sim_snapshot` / `sim_expiry` → `compute.sim_fetch(symbol, lazy=True,
  store=PUBLIC_SIM)` / `sim_fetch_expiry(..., store=PUBLIC_SIM)`, where
  `PUBLIC_SIM = compute.SimStore(limit, ttl)` built from
  `public_tools.limits()`. Publish the meta (strikes, expirations, spot —
  no chain) to `result_view`.

**Math requests** (order: `invalid` → `expired` → `load_first` → `cached` →
compute; no budget, no window):
- `price` → `load_first` unless `public_chain.held(symbol)`; derive
  `price_rows` with `calculator.strikes_window`'s logic **server-side** (copy
  the ±N window over the held front-expiry strikes into a small pure helper
  here, tested); check every option leg's strike is on the held ladder
  (`off_ladder`); `compute.calc_compute(**args_without_kind_symbol,
  price_rows=rows)`.
- `iv` → mark from the held chain (`chain_grid.extract_premium` has a service
  twin? — if not, read `callExpDateMap`/`putExpDateMap` directly in a small
  pure helper, tested); `compute.calc_iv(spot, strike, option_type, mark,
  expiry)`; publish **only** `{"iv": <percent>}`.
- `sweep` → `load_first` unless `PUBLIC_SIM.get(symbol)`;
  `compute.sim_run(symbol, legs=legs, dt=dt, store=PUBLIC_SIM)`; publish
  `whatif_rows`, `whatif_baseline`, `spot`, `legs`, `dt` — **not** `ivshock`
  (the Volatility tab is not published).

Add to `shared/public_tools.py`: `OUTCOMES` / `OUTCOME_TEXT` =
`public_rescue`'s plus `"load_first": "Load the symbol first."`.

`app.py`: extend `extra_consumers` with
`(public_tools.TOOLS_STREAM, tools_public.handle_tools)` and
`(public_tools.MATH_STREAM, tools_public.handle_math)`. Update the pin in
`test_finder_public.py::test_the_service_consumes_the_public_stream_on_its_own_loop`
to the four-entry tuple, with a comment.

**Step 1: Failing tests** — `test_tools_public.py`, the
`test_rescue_public.py` pattern (stub `calc_load_symbol`, `_fetch_thin_runs`,
`rate_trade.rate`, `_fetch_sim_snapshot`, `calc_compute`, `calc_iv`, `sim_run`,
record calls). At minimum:
- each refusal above, asserting no stubbed Schwab function ran;
- `price`: `price_rows` is computed server-side and a sent `price_rows` is
  impossible (the builder drops it);
- `iv` publishes one key, `iv`, and no mark;
- `sweep` publishes no `ivshock`;
- `rate` result has no Paper book line; a `rate` `error` is not cached;
- `sim_snapshot` writes `PUBLIC_SIM`, never `compute._SIM_SNAPSHOTS`;
- the budget is shared: exhaust it through Rescue, then a `chain` request
  here answers `budget`;
- a Redis error in the status read never escapes either handler;
- the status view contains no symbol or strike.

**Step 2–4:** Run — FAIL, implement, PASS. Mutation-check the window gate and
the budget gate.

**Step 5: Commit** — `feat(public tools): the tools and math workers`.

---

## Phase C — Tier 1

### Task 8: the two public write functions

**Files:**
- Modify: `webgui/bus_client.py` (after `request_public_rescue`)
- Test: `webgui/tests/test_bus_client.py`

**Step 1: Failing tests** (copy the Rescue block's shape):
- `request_public_tool(raw_request)` writes only `public_tools.tools_command(
  raw_request)` onto `public_tools.TOOLS_STREAM`; `request_public_math(
  raw_request)` the same for math. Both allowed on a read-only process; both
  raise `ValueError` before writing on a refused request; neither reaches
  `cmd:options`.
- The AST pin: one parameter each (`raw_request`), one `enqueue_command`
  whose stream is `public_tools.TOOLS_STREAM` / `MATH_STREAM` and whose
  command is the builder's result.
- **Update** `test_the_public_origin_has_exactly_three_write_functions`: it
  becomes "exactly five", with `request_public_math` and `request_public_tool`
  added to the expected list. Rename it accordingly and keep the docstring's
  "adding one must be a decision" sentence.

**Step 2–4.** **Step 5: Commit** — `feat(public tools): the two public write functions`.

---

### Task 9: gate every private enqueue in `calculator.py` and `simulator.py`

**Files:**
- Modify: `webgui/pages/options/calculator.py`, `webgui/pages/options/simulator.py`
- Test: `webgui/tests/test_live_commands.py` (existing guard — do not edit it)

Once Task 14 publishes these two modules, `test_live_commands.py` will require
every function containing a `bus_client.request(...)` to open with
`if not _may_enqueue: return`. Do it now, in the `rescue.py` way (read commit
b3a4ade's `rescue.py` diff): add `render(public=False)`; if `public`, hand off
to the live module and return; then `import shell as _shell_gate;
_may_enqueue = _shell_gate.may_enqueue()`; then insert the guard as the first
statement (after any docstring) of each enqueuing function. A call made
directly in `render`'s body must move into a small gated function.

**Step 1:** Temporarily add the two modules to a local copy of the guard's
enumeration to see it fail (or skip ahead: it fails for real in Task 14).
**Step 3:** Add the gates. **Step 4:** Run `tests/test_options_calculator*`,
`tests/test_options_simulator*`, `tests/test_calc*`, `tests/test_sim*` — the
private pages must be unchanged. **Step 5: Commit** —
`refactor(calculator, simulator): gate every enqueue on the origin`.

---

### Task 10: tab-storage hand-off helpers

**Files:**
- Create: `webgui/pages/options/public_handoff.py`
- Modify: `webgui/live_main.py` (set NiceGUI's tab-storage max age to 1 hour
  before `ui.run` — find the setting name in the installed NiceGUI:
  `grep -rn "max_tab_storage_age" .venv/Lib/site-packages/nicegui`)
- Test: `webgui/tests/test_public_handoff.py`

A PURE part (`position_payload(symbol, legs)`: normalized, only symbol + legs
with option_type/side/strike/expiry/qty/premium, max 8 legs, validated with
`public_tools`'s leg cleaner; `seed_from(payload)` → `(symbol, legs)` or
`None` for anything malformed) and a thin NiceGUI part (`write(payload)` /
`read()` over `app.storage.tab` under the key `"calc_position"`, each wrapped
so a missing or disconnected client is a no-op).

**Tests:** round trip; malformed payload → None; a stored payload with extra
keys is stripped; `live_main` sets the max age (source-level assert).

**Commit** — `feat(public tools): the Calculator-to-Simulator hand-off, per tab`.

---

### Task 11: the public Calculator page — `calc_live.py`

**Files:**
- Create: `webgui/pages/options/calc_live.py`
- Modify: `webgui/tests/test_no_inline_style.py` (add the file)
- Test: `webgui/tests/test_calc_live.py`

Build from the private page's own module-level pieces — **do not copy them**:
`entry_panel`, `leg_editor.build_leg_editor(layout="table", ...)`,
`calculator._render_metrics`, `calculator._render_grid`,
`calculator.fill_stock_premiums`, `calculator.max_dte_from_legs`,
`strategy_menu.build_strategy_menu`, and `detail` for Rate My Trade.

Behaviour:
- Load → `request_public_tool({"kind": "chain", "symbol": ...})`; poll the
  answer; read `pub_chain`. With no `"quotes"` in it: no chain grid; the leg
  editor's `strikes_for` / `expiries_for` / `listed_expiries_for` /
  `on_expiry_needed` come from the chain payload (copy `rescue_live`'s three
  pure helpers into a shared module rather than duplicating — move
  `listed_expirations` / `loaded_expirations` / `ladder_strikes` from
  `rescue_live.py` to a new `pages/options/pub_chain_view.py` and import them
  in both); `price_for=None`; `delta_for=None`. With `"quotes"`: pass
  `entry_panel` the grid and `price_for` / `delta_for` reading the quotes.
- After a load, request `iv` for the leg nearest spot and fill the volatility
  assumption with the answer.
- Every edit (0.3 s debounce, as the private page) →
  `request_public_math({"kind": "price", ...})`; one pending request per key;
  draw metric cards + matrix from the result.
- Rate My Trade → `request_public_tool({"kind": "rate", ...})`; render with
  `detail`.
- **Open in Simulator** → `public_handoff.write(...)` then
  `ui.navigate.to("/simulator")`.
- On every leg change → `public_handoff.write(...)`. **Never** read it.
- Per-visitor limits: two `visitor_limit.Limiter`s (tools, math).
- Refusals worded from `public_tools.OUTCOME_TEXT`; nothing raw.
- No Expected Move button; no `page_state`; no `shared_position`.

**Tests** (the `test_rescue_live.py` harness): page load spends nothing; Load
sends one tools request and fills the strip; quotes off → no grid element, no
Bid/Mark/Ask select; quotes on (patch the chain payload) → grid present; an
edit sends one math `price` request (and a second identical edit sends none
while pending); a result draws the six metric cards; Rate My Trade sends one
`rate`; Open in Simulator writes the hand-off; the module source never reads
tab storage (AST: no `public_handoff.read`); the module enqueues only through
`request_public_tool` / `request_public_math` (copy the Rescue source test);
names no owner key (`calc_chain`, `calc_result`, `calc_iv`, `calc_rating`,
`shared_position`, `page_state`).

**Commit** — `feat(public tools): the public Calculator page`.

---

### Task 12: the public Simulator page — `sim_live.py`

**Files:**
- Create: `webgui/pages/options/sim_live.py`
- Modify: `webgui/tests/test_no_inline_style.py`
- Test: `webgui/tests/test_sim_live.py`

From the private pieces: `entry_panel`, `leg_editor` (table layout),
`simulator.whatif_figure`, `sim_view.position_tiles`, `sim_view.days_range`,
`sim_view.whatif_readout`. One `ui.highchart` created at page build with an
explicit `chart.height` (CLAUDE.md: a chart mounted hidden collapses).

Behaviour:
- On build: `public_handoff.read()`; if it seeds, set symbol + legs and send
  one `sim_snapshot` request; otherwise a default template, no request.
- Load → `sim_snapshot`; far expiration → `sim_expiry`.
- Days slider (0–`days_range` max) and every leg edit → debounced
  `sweep` (0.4 s, as the private page); the price-offset slider redraws the
  overlay locally, no request.
- `load_first` answer → "Load the symbol first." with Load enabled.
- No tab strip, no volatility multiplier, no History.

**Tests:** a seeded tab sends exactly one `sim_snapshot`; an unseeded one sends
nothing; a slider move sends one `sweep` after the debounce; the offset slider
sends nothing; `load_first` is worded; the source has no `sim_replay`, no
`mult`, no `TAB_VOLATILITY` / `TAB_HISTORY`; enqueues only through the two
public functions; names no owner key (`sim_meta`, `sim_chain`, `sim_result`,
`sim_replay`, `_SIM_SNAPSHOTS`).

**Commit** — `feat(public tools): the public Simulator page (Price & Time)`.

---

### Task 13: two-visitor isolation test

**Files:** Test: `webgui/tests/test_public_tools_isolation.py`

Build the Calculator page in two separate NiceGUI clients (two `ui.card`
roots with distinct `app.storage.tab` stand-ins — patch `public_handoff`'s
storage accessor with a dict per client), set different legs in each, build a
Simulator in each, assert each seeds from its own. Plus: the public Calculator
built after a private Calculator in the same process does not show the
private page's `_LAST_CALC` state.

**Commit** — `test(public tools): visitors never see each other's position`.

---

## Phase D — publishing, docs, rollout

### Task 14: publish the two screens

**Files:**
- Modify: `webgui/live_screens.py` — two `Screen`s after Rescue:
  `Screen("calculator", "/calculator", "Calculator", "options.calculator",
  "/options/calculator", kwargs={"public": True})` and
  `Screen("simulator", "/simulator", "Simulator", "options.simulator",
  "/options/simulator", kwargs={"public": True})`, each with a comment in the
  Rescue entry's style.
- Modify: `webgui/tests/test_live_screens.py` (22 → 24), the module docstring
  count.
- Modify: `deploy/site/live.html` (two tiles after Rescue's; the "Twenty-two"
  prose → "Twenty-four", and "the Strategy Finder and Rescue take a symbol or a
  trade" → name all four tools), `deploy/site/{index,live,gallery,report}.html`
  Tools menu: Calculator becomes a link, a Simulator item is added.
- Modify: `deploy/tests/test_site.py` `TOOLS` — add `("simulator", "Simulator")`.

Run `test_live_commands.py`: it now enumerates `options.calculator` and
`options.simulator` — it must pass because of Task 9. Run the site suite.

**Commit** — `feat(public): publish the Calculator and Simulator`.

---

### Task 15: config catalogue, runbook, docs

- `webgui/config_schema.py`: a `_TOOLS_PUBLIC` `ConfigFile` for every key in
  `tools_public.toml`, plus `[windows.tools_public]` in the Sessions section;
  `config/sessions.toml` + `shared/market_calendar.py` get the window
  (08:40–15:00, as Rescue). `test_config_schema.py` must pass.
- `docs/dev-prod-environments.md`: steps **4e** (`(%W~cmd:tools_public +xadd)`)
  and **4f** (`(%W~cmd:tools_public_math +xadd)`), each marked "Not yet
  applied", each with its two probe lines, in the 4d style.
- `CLAUDE.md`: allow-list gains `shared.public_tools`; the public-screens
  section says twenty-four screens and **three** write paths, and records the
  held-chain rule, the separate snapshot store, and the tab-storage hand-off;
  the route table rows for `/options/calculator` and `/options/simulator` note
  their public copies. Correct in place; no appended corrections.
- `docs/webgui-routes.md`: two rows; count.
- `docs/manuals/user-guide/user-guide.md`: a paragraph for each tool (what a
  visitor can do, what is hidden while quotes are off, the limits, where they
  are configured), then rebuild: `(cd docs/manuals && python build_docs.py user-guide)`.
- `docs/CHANGELOG.md`: a new top entry, "built, not promoted, ACL not applied,
  snapshot cost unmeasured".

**Commit** — `docs(public tools): catalogue, runbook, manuals`.

---

### Task 16: verify end to end, then review

1. Run every suite listed in *Rules*; compare the failing SET, not the count.
2. Local harness, as done for Rescue: a scratch runner that serves
   `options.calculator` and then `options.simulator` with
   `--kwargs '{"public": true}'` through `tools/ui_harness.py`, with the real
   `tools_public` workers polling the same fake bus in a thread
   (**non-blocking** `consume_commands(block_ms=None)` + a short sleep — a
   blocking read holds the fakeredis lock and stalls the page), and only
   `calc_load_symbol`, `_fetch_thin_runs`, `rate_trade.rate` and
   `_fetch_sim_snapshot` faked. Walk: Load → price edits → Rate My Trade →
   Open in Simulator → the Simulator seeds and draws Price & Time → a
   days-slider move redraws. Check phone width (no page-level horizontal
   scroll).
3. Dispatch an independent reviewer (read-only) over the whole branch with the
   same brief the Rescue review had: security of the three public write paths,
   correctness, private pages unchanged, tests that pin their claims. Fix what
   it finds, with tests, and mutation-check the new ones.
4. Report to the owner: what was verified and how, what was not (live Schwab
   costs), and the rollout order: runbook 4d → 4e → 4f → promote after the
   close → measure → tune.
