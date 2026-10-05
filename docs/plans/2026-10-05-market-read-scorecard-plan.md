# Market Read Scorecard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A "Market read" panel on the Desk: six rows, each a Tailwind / Headwind /
Neutral / No reading chip decided by rule from views the services already publish,
recomputed every 15 minutes in the regular session, with what changed since the
last update.

**Architecture:** A pure module in the market service turns three published views
(`market:dashboard`, `options:matrix`, `options:flow_sides` + `options:flow_alerts`)
into one reading. The service's existing loop publishes it as `cache:market:read`
on each clock slot. The Desk draws it with module-level builders.

**Tech stack:** Python 3.11, pytest, Redis via `shared.bus`, pydantic contract,
NiceGUI page kit.

**Design:** `docs/plans/2026-10-05-market-read-scorecard-design.md`. Read it first.

---

## Constraints

1. `desk.render` is at 781 of 790 lines and **22 of 22 nested functions**
   (`webgui/tests/test_render_size.py`). No new nested function. The painter is
   module-level; the wiring is a handful of lines.
2. Tier 1 imports nothing from `services`. The verdict codes are the contract; the
   words and colours live in the page.
3. Absence is not a reading: a missing or non-finite input makes the row `none`,
   never `neutral`. Use `shared.numeric.finite`; no new private `_num`/`_finite`.
4. A guard of 15+ lines calls `_degrade.degraded`.
5. Every threshold is in `config/market_read.toml` with a `config_schema` entry.
6. Never weaken an existing assertion. Test files are written with the editor.
7. Two decimals through `pages/fmt.py` on every percentage and price.

**Commands** (Git Bash, worktree root)

```bash
PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
"$PY" -m pytest services/market_svc -q
"$PY" -m pytest shared -q
(cd webgui && "$PY" -m pytest . -q)
"$PY" -m pytest services/tests tests -q
```

---

### Task 1: Configuration

**Files:** create `config/market_read.toml`; modify `repo_paths.py`
(`MARKET_READ_TOML`); create `shared/market_read_config.py` (a `toml_loader` over
the defaults in the design, like `shared/marketdata_config.py`); modify
`webgui/config_schema.py` (a new `ConfigFile`, no restart: the service reads it
per slot); tests in `shared/tests/test_market_read_config.py` and the existing
`webgui/tests/test_config_schema.py`.

Tests: the defaults; the shipped file equals the defaults; a bad value falls back
per key (`interval_min` to 15 unless it is 15 or 30; a share outside 0..1; a
negative threshold; `symbols` not a list); `enabled`/`public` on only for a
literal `true`.

Expose `load()` returning a cleaned, read-only mapping and `reset()`.

**Commit:** `feat(config): market_read.toml`

### Task 2: The six rows (pure)

**Files:** create `services/market_svc/market_read.py` and
`services/market_svc/tests/test_market_read.py`.

Codes: `TAILWIND = "tailwind"`, `HEADWIND = "headwind"`, `NEUTRAL = "neutral"`,
`NONE = "none"`. Each row function returns
`{"key", "verdict", "facts": {...}}` and never raises.

```python
def tiles_by_name(dashboard) -> dict          # {display: tile}; total over junk
def direction(dashboard, cfg) -> dict         # facts: spx_pct, ndx_pct
def breadth(dashboard, cfg) -> dict           # facts: advancing, declining, share
def structure(matrix, cfg) -> dict            # facts: per symbol {spot, flip, ceiling, room_pct, mode, state}
def volatility(dashboard, cfg) -> dict        # facts: vix, vix_pct, vix1d, vix3m, spx_pct
def flow(sides, alerts, cfg) -> dict          # facts: call_lean, put_lean, calls, puts, contracts; "estimate": True
def cross_asset(dashboard, cfg) -> dict       # facts: per tile {pct, state}; risk_on, risk_off
```

Tests, per row: tailwind, headwind, neutral, and at least two `none` cases
(missing input; non-finite input). Plus:

- direction: one index up and one down is neutral; a bound is inclusive.
- breadth: counts only `BREADTH_CATEGORIES`, skips a `basket` tile, reads
  direction from `color_state` (so a red VIX is not a decline); zero counted is
  `none`. `BREADTH_CATEGORIES` is added to `market_svc/symbols.py` and pinned
  equal to the webgui page's copy in `shared/tests/test_cross_tier_mirrors.py`.
- structure: `gex_regime == "above"` is long gamma; a net gamma of exactly 0.0 is
  `none`; SPY and QQQ disagreeing is neutral with both shown; `room_pct` is
  `(ceiling - spot) / spot * 100`; price ABOVE the ceiling counts as at it.
- volatility: VIX up on a day SPX is DOWN is not a headwind by that rule; VIX1D
  above VIX is a headwind on any day.
- flow: sides are joined to alerts by id; a sides view dated another day is
  `none`; fewer than `min_contracts` is `none`; lean is pooled by volume
  (`(Σbought − Σsold) / Σtotal × 100`), calls and puts apart; both leaning bought
  is neutral.
- cross_asset: states come from the tile's `color_state`; `flat`/`no_data` count
  for neither; fewer than two usable tiles is `none`.

**Commit:** `feat(market): the six market-read rows`

### Task 3: Assembling a reading, slots, history

**Files:** same module and test file.

```python
def slot_due(now, last_slot, interval_min) -> str | None   # "HH:MM" of the slot now due, else None
def next_slot(slot, interval_min) -> str | None            # None after the 15:00 slot
def tally(rows) -> dict                                    # {"tailwind": n, "headwind": n, "neutral": n, "none": n}
def build(inputs, cfg, *, date, slot, ts, previous=None) -> dict
```

- Slots: clock multiples of `interval_min` from the first one strictly after the
  08:30 CT open through 15:00 inclusive, trading days only
  (`shared.market_calendar`). A late tick still fires the slot it is in, once.
- `build` runs the six rows, each inside its own guard (a failing row is `none`
  and is counted through `_degrade.degraded("market.read.<key>")`), attaches each
  row's `prev` (`{"verdict", "facts"}` from `previous`), and appends
  `{"slot", "verdicts"}` to `history` (kept for the day; a new date starts empty).
- An input view older than `stale_after_sec` (by its envelope `ts`) or dated
  another day is passed to the row as absent.

Tests: the slot table across the open, mid-session, the close, a weekend and a
holiday; 15 and 30 minutes; `final` only on the 15:00 slot; `prev` and `history`
across two builds; a new date drops the history; one row raising leaves five.

**Commit:** `feat(market): assemble and schedule the market read`

### Task 4: Publishing

**Files:** `shared/contracts/market.py` (`MarketRead`: `date`, `ts`, `slot`,
`next_slot`, `final`, `public`, `tally`, `rows`, `history`);
`services/market_svc/handlers.py` (`CACHE_READ`, `publish_read`);
`services/market_svc/scheduler.py` (`refresh_read(bus, state)` called from `loop`
after the dashboard publish, in its own guard); tests in
`services/market_svc/tests/test_market_read_publish.py`.

- `refresh_read` reads the config; does nothing when not enabled or no slot is
  due; reads the four input views with their envelopes; builds; publishes with
  `skip_unchanged`.
- On its first call it reads `cache:market:read` back: the same date restores
  `last_slot` and `history`.
- Tests: a slot due publishes once and the same slot does not publish again; a
  restart mid-session does not republish the slot and keeps `prev`; **a producer
  test** that feeds an empty dashboard and asserts the published rows are `none`;
  disabled publishes nothing; `public` follows the config and fails closed; a
  failing build leaves the last reading and counts a degrade.

**Commit:** `feat(market): publish cache:market:read`

### Task 5: The Desk panel

**Files:** `webgui/pages/desk.py`, `webgui/pages/copy.py` if a sentence is
shared, `webgui/tests/test_desk.py`, `webgui/tests/test_render_size.py`
(ceilings may only fall).

Pure builders, module level:

```python
READ_VIEW = "market:read"
def read_view_shown(view)            # the public filter; event loop only
def read_header(view, now)           # {"slot", "next", "tally_text", "state": live|close|stale|waiting}
def read_rows(view)                  # [{key, question, reading, since, verdict_word, chip_class, estimate}]
def _paint_read(body, view, now)     # module-level painter
```

- Verdict words and chip classes come from one finite map
  (`tailwind/headwind/neutral/none`); an unknown code is "No reading".
- Reading text per row is built from `facts` with `pages/fmt`; a missing fact is
  `fmt.NO_READING`, never a zero. The flow reading opens with `≈`.
- `since`: "was <word>" when the verdict changed; else the change in the row's
  main number (direction: SPX points of percent; breadth: advancing count;
  volatility: VIX; flow: call lean); else "unchanged"; "first reading" with no
  `prev`.
- `state`: `waiting` with no view; `stale` when the date is not today or, in the
  session, the reading is older than two intervals; `close` when `final`.
- Wiring: add the view to `VIEWS` and a `read` entry to `_REGION_VIEWS`; the panel
  goes above the Market Summary. No new nested function in `render`.

Tests: each builder over a full payload and over junk; every verdict code; every
`since` branch; every header state; the public filter both ways; the size test.

Verify on the page harness with a seeded view: all four chips, a stale reading,
the close reading, and a narrow width.

**Commit:** `feat(webgui): the Market read panel on the Desk`

### Task 6: Documentation

`webgui/page_help.py` (`/desk`); User Guide, Reference Guide, Technical Reference
(the six rules and thresholds), API Reference (`cache:market:read`); rebuild with
`docs/manuals/build_docs.py`; `docs/webgui-routes.md`;
`docs/reference/config-files.md`; `docs/CHANGELOG.md`. `CLAUDE.md` gets the
config file's name in its list and nothing else unless an invariant changed.

**Commit:** `docs: the Market read scorecard`

### Task 7: Verify

Use @superpowers:verification-before-completion.

1. Every suite; the failing and skipped SETS match the baseline (webgui 1 skip,
   options-scanner 2 skips, sentiment 1 xfail).
2. Lint gate and pyright clean.
3. Request review with @superpowers:requesting-code-review, asking for every
   changed assertion in a pre-existing test.

### After promote (the user decides when)

Read-only, on prod, in the session: `cache:market:read` has today's date and a
slot within the last interval; six rows; no `market.read.*` degrades on
`/health`; the public Desk shows the panel. Then report how often each row read
`none` and how often each chip changed over the day.
