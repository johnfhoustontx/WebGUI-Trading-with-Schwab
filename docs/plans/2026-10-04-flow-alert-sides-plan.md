# Bought or Sold on Flow Alerts — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give every contract-level flow alert (unusual volume, outsized bet) an
estimated bought / sold / unlabelled share, and the next session an
opened-or-closed verdict from the open-interest change.

**Architecture:** A pure tally module labels each contract's new volume from the
one-minute chain poll (the whole session) and from a level-one stream subscribed
once the alert fires (after the alert). A small stateful sibling module owns the
session state, persists flagged contracts to a new table in `gex_history.db`,
resolves yesterday's rows from today's chains, and publishes two cache views the
Flow Alerts page and the Desk read.

**Tech stack:** Python 3.11, pytest, SQLite, Redis via `shared.bus`, FastAPI SSE
(proxy), NiceGUI page kit.

**Design:** `docs/plans/2026-10-04-flow-alert-sides-design.md`. Read it first.

---

## Before you start

Read these. Each one is a rule this plan leans on.

- Root `CLAUDE.md`: the Tier-1 import allow-list, "configurable by default",
  "A swallowed exception must leave a trace", "Three size ceilings can only be
  lowered", the NaN rule ("a quote or Greek that is not usable is ABSENT").
- `docs/plans/2026-10-01-hiro-alert-design.md` §1: the measurement rules this
  build reuses (`classify_side`, the high-water mark).
- `services/options_svc/hiro.py` lines 43–86: `classify_side` and `_contracts`.
- `services/options_svc/compute.py` around `def on_chain` (search for it): the hook.
- `services/options_svc/handlers.py` `run_flow_alerts`: where alerts are built.
- `webgui/pages/options/flow.py` `_shown`, `alert_rows`, `alert_detail`.

**Hard constraints**

1. `services/options_svc/compute.py` is 10,538 lines against a ceiling of 10,540
   (`tests/test_compute_module_shape.py`). This plan adds **one** line to it and
   edits one in place. Do not add more; put logic in the sibling modules.
2. `desk.render` has a size ceiling too (`webgui/tests/test_render_size.py`).
   Module-level helpers are free; lines inside `render()` are not.
3. New service code must not import from `compute`.
4. No new private `_num` / `_finite` helper: use `shared.numeric.finite`.
5. A `try/except` guard of 15+ lines must call `_degrade.degraded("<area>")`.
6. Never weaken or narrow an existing assertion to make a test pass. If an
   existing test fails, stop and work out why.
7. Write test files with the editor, never a shell heredoc.
8. Webgui: Tailwind classes only, page kit only (`kit.table`, `kit.button`, …),
   two decimals through `pages/fmt.py`.

**Commands** (Git Bash, from the worktree root)

```bash
PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
"$PY" -m pytest services/options_svc -q                    # options service
(cd options-scanner && "$PY" -m pytest tests -q -p no:randomly)
(cd schwab-proxy && "$PY" -m pytest tests -q)
(cd webgui && "$PY" -m pytest . -q)
"$PY" -m pytest services/sentiment_svc -q                  # reads the proxy tick shape
```

### Task 0: Baseline

**Step 1:** Run the five suites above. Save each suite's failing and skipped test
names to a scratch file outside the repo.

**Step 2:** `git status` must be clean. Record `git rev-parse HEAD`.

Later tasks compare the failing SET against this, never the count.

---

### Task 1: The proxy's option feed carries total volume

**Files:**
- Modify: `schwab-proxy/schwab_proxy.py` (`_L1_OPTION_FIELDS`, its comment, the
  `_normalize_level1_option` docstring)
- Test: `schwab-proxy/tests/test_option_stream.py`

**Step 1: Write the failing tests.** In `test_option_stream.py`, add
`"total_volume": None` to the `_expected` base dict, then add:

```python
def test_normalize_option_total_volume_numeric_key():
    # 8 = TOTAL_VOLUME (LevelOneOptionFields).
    out = _normalize_level1_option({"key": "SPXW_V", "8": 1234})
    assert out["total_volume"] == 1234.0 and isinstance(out["total_volume"], float)


def test_normalize_option_total_volume_by_enum_name():
    out = _normalize_level1_option({"key": "SPXW_V", "TOTAL_VOLUME": "55"})
    assert out["total_volume"] == 55.0
```

**Step 2:** `(cd schwab-proxy && "$PY" -m pytest tests/test_option_stream.py -q)`
→ FAIL (`KeyError: 'total_volume'` and the `_expected` comparisons).

**Step 3: Implement.** Add one entry to `_L1_OPTION_FIELDS`:

```python
    "total_volume": ("TOTAL_VOLUME", "8"),
```

Extend the comment above the map (`8 = TOTAL_VOLUME`) and the docstring's key list.

**Step 4:** Proxy suite passes. Then run `services/sentiment_svc` — its consumer
reads ticks by key and must be unaffected.

**Step 5: Commit** `feat(proxy): total volume on the option stream tick`.

---

### Task 2: Configuration

**Files:**
- Modify: `services/options_svc/flow_alerts.py` (`_DEFAULTS`)
- Modify: `config/flow_alerts.toml`
- Modify: `webgui/config_schema.py` (`_FLOW` sections)
- Test: `services/options_svc/tests/test_flow_alerts.py`,
  `webgui/tests/test_config_schema.py` (should pass unchanged once the catalogue
  has the keys; it fails first)

**Step 1: Failing test** in `test_flow_alerts.py`:

```python
def test_sides_and_followup_defaults():
    cfg = flow_alerts._merge(flow_alerts._DEFAULTS, {})
    assert flow_alerts.section(cfg, "sides") == {
        "enabled": True, "public": True, "stream": True,
        "stream_max_contracts": 200}
    assert flow_alerts.section(cfg, "followup") == {
        "enabled": True, "opened_ratio": 0.5, "closed_ratio": -0.5,
        "keep_sessions": 20}


def test_sides_scalar_override_falls_back_to_defaults():
    cfg = flow_alerts._merge(flow_alerts._DEFAULTS, {"sides": 5})
    assert flow_alerts.section(cfg, "sides")["stream_max_contracts"] == 200
```

**Step 2:** Run → FAIL.

**Step 3: Implement.** Add both tables to `_DEFAULTS` with a short comment naming
the design doc. Add the same two tables to `config/flow_alerts.toml` with one
comment per key (say what "public" does and that `stream_max_contracts` was sized
on 2026-10-02's 175 alerts). Add two `Section`s to `_FLOW` in `config_schema.py`,
modelled on the `hiro.*` fields:

| Key | Kind | Label |
|---|---|---|
| `sides.enabled` | bool | Estimate bought and sold on flow alerts |
| `sides.public` | bool | Show the estimate on the public screens |
| `sides.stream` | bool | Stream a contract after its alert |
| `sides.stream_max_contracts` | int, 0–1000 | Most contracts streamed in a day |
| `followup.enabled` | bool | Read next-day open interest |
| `followup.opened_ratio` | float, 0–1, step 0.05 | "Mostly opened" at |
| `followup.closed_ratio` | float, −1–0, step 0.05 | "Mostly closed" at |
| `followup.keep_sessions` | int, 1–250 | Sessions kept |

Write the help text from the reader's side, whole words, no "OI".

**Step 4:** `services/options_svc` and `webgui/tests/test_config_schema.py` pass.

**Step 5: Commit** `feat(config): [sides] and [followup] for flow alerts`.

---

### Task 3: The detectors name the contract

**Files:**
- Modify: `services/options_svc/flow_alerts.py` (`detect_uoa`, `detect_big_delta`)
- Test: `services/options_svc/tests/test_flow_alerts.py`

**Step 1: Failing tests.** Using the existing chain helpers in that file, assert
that each detector's output dict carries `"osi"` equal to the contract's
`"symbol"`, and `None` when the contract has no `"symbol"`. For `big_delta` also
assert `"oi"` is absent (it is not added; the tally supplies it).

**Step 2:** Run → FAIL (`KeyError: 'osi'`).

**Step 3: Implement.** Add `"osi": c.get("symbol")` to the dict each detector
appends. Nothing else changes: the alert `id` and the cooldown key keep their
shape, so no existing alert identity moves.

**Step 4:** Options suite passes. If any snapshot-style test compares whole alert
dicts, add the key to its expected value; do not relax the comparison.

**Step 5: Commit** `feat(options): flow detectors carry the contract symbol`.

---

### Task 4: The pure tally — `flow_sides.py`

**Files:**
- Create: `services/options_svc/flow_sides.py`
- Create: `services/options_svc/tests/test_flow_sides.py`

**Step 1: Failing tests.** Reuse the chain shape from `test_hiro.py` (`_c`,
`_chain`), adding `openInterest`. Cover, one test each:

```python
def test_first_sight_of_an_unseeded_symbol_is_unlabelled():
    # Volume the service did not watch print: counted, never labelled.
    book = {}
    fs.advance(_chain(calls=[_c("C1", 1000, last=1.10)]), book, seeded=False)
    assert fs.tally(book["C1"]) == {"bought": 0.0, "sold": 0.0, "unlabelled": 1000.0}


def test_new_volume_at_the_ask_is_bought():
    book = {}
    fs.advance(_chain(calls=[_c("C1", 1000, last=1.05)]), book, seeded=False)
    fs.advance(_chain(calls=[_c("C1", 1400, last=1.10)]), book, seeded=True)
    assert fs.tally(book["C1"]) == {"bought": 400.0, "sold": 0.0, "unlabelled": 1000.0}
```

and likewise:

- at the bid → sold; at the midpoint or a crossed quote → unlabelled;
- a contract first seen with volume in a **seeded** symbol books that volume
  with a label (it was at zero last minute);
- `label=False` (a poll gap) books the change as unlabelled;
- a glitch read of 0 books nothing and a later real read does not re-book
  (high-water mark);
- a contract at zero volume creates no entry;
- `totalVolume` NaN / None / negative is skipped;
- `openInterest` is stored when finite and ≥ 0, and a later NaN does not erase it;
- **the invariant:** after any sequence, bought + sold + unlabelled equals the
  latest volume;
- `advance` returns the number of contracts that booked;
- a non-dict chain returns 0 and leaves the book alone.

Stream (`advance_tick`):

- the first tick of a contract only seeds (it books nothing: "since the alert");
- ticks are **deltas**: a tick carrying only `total_volume` is labelled from the
  last/bid/ask merged from earlier ticks;
- `label=False` books the change as unlabelled;
- a tick with no symbol or no usable volume changes nothing.

Verdict, parametrised:

| oi_prev | oi_next | volume | expiry vs session | want |
|---|---|---|---|---|
| 1000 | 1600 | 1000 | later | `("opened", 0.6)` |
| 1000 | 400 | 1000 | later | `("closed", -0.6)` |
| 1000 | 1100 | 1000 | later | `("mixed", 0.1)` |
| 1000 | 1500 | 1000 | later | `("opened", 0.5)` — the bound is inclusive |
| any | any | any | same day | `("expired", None)` |
| None / NaN | … | … | later | `("none", None)` |
| 1000 | 1600 | 0 | later | `("none", None)` |

**Step 2:** Run → FAIL (module missing).

**Step 3: Implement.**

```python
"""Bought / sold / unlabelled volume per option contract. PURE (stdlib only).

An ESTIMATE: Schwab publishes no time-and-sales tape, so new volume is labelled
from where the latest trade price sits against the bid and ask -- once a minute
on the polled chain, per tick on the level-one stream. The rule is
``hiro.classify_side``, shared with the hedging-flow model.

Volume the service did not watch print (before a restart, across a poll gap) is
UNLABELLED, never dropped, so bought + sold + unlabelled always equals the
contract's volume. Design: docs/plans/2026-10-04-flow-alert-sides-design.md.
"""
from shared.numeric import finite as _finite

from services.options_svc.hiro import _contracts, classify_side

# One contract's entry is a list, not a dict: every contract with volume in
# every fetched chain has one, and a list is about a third of the memory.
HW, BOUGHT, SOLD, UNLABELLED, OI = range(5)


def new_entry() -> list:
    return [0.0, 0.0, 0.0, 0.0, None]


def tally(entry) -> dict:
    return {"bought": entry[BOUGHT], "sold": entry[SOLD],
            "unlabelled": entry[UNLABELLED]}


def _book(entry, dv, side) -> None:
    entry[BOUGHT if side > 0 else SOLD if side < 0 else UNLABELLED] += dv


def advance(chain, book, *, seeded, label=True) -> int:
    """Book one fetched chain's new volume into ``book`` ({contract symbol:
    entry}), IN PLACE. Returns how many contracts booked volume.

    ``seeded`` -- this symbol was already polled this session, so a contract
    with no entry stood at zero volume last time and its volume is new.
    Unseeded, that volume predates the watch and is unlabelled.
    ``label`` -- False after a poll gap: several minutes of volume must not
    take one minute's bid/ask label."""
    if not isinstance(chain, dict):
        return 0
    booked = 0
    for _is_call, c in _contracts(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None or vol < 0:
            continue
        entry = book.get(osi)
        if entry is None:
            if vol <= 0:
                continue
            entry = book[osi] = new_entry()
            dv, can_label = vol, seeded and label
        else:
            dv, can_label = vol - entry[HW], label
        if dv > 0:
            side = (classify_side(c.get("last"), c.get("bid"), c.get("ask"))
                    if can_label else 0)
            _book(entry, dv, side)
            entry[HW] = vol
            booked += 1
        oi = _finite(c.get("openInterest"))
        if oi is not None and oi >= 0:
            entry[OI] = oi
    return booked


def advance_tick(book, quotes, tick, *, label=True) -> bool:
    """Book one level-one tick. ``quotes`` is ``{contract symbol: {last, bid,
    ask}}`` and is merged IN PLACE: a tick after the first carries only the
    fields that changed. A contract's first tick only seeds -- the stream
    measures what trades AFTER the alert. True when volume was booked."""
    if not isinstance(tick, dict):
        return False
    osi = tick.get("symbol")
    if not osi:
        return False
    q = quotes.setdefault(osi, {})
    for key in ("last", "bid", "ask"):
        v = _finite(tick.get(key))
        if v is not None:
            q[key] = v
    vol = _finite(tick.get("total_volume"))
    if vol is None or vol < 0:
        return False
    entry = book.get(osi)
    if entry is None:
        entry = book[osi] = new_entry()
        entry[HW] = vol
        return False
    dv = vol - entry[HW]
    if dv <= 0:
        return False
    side = classify_side(q.get("last"), q.get("bid"), q.get("ask")) if label else 0
    _book(entry, dv, side)
    entry[HW] = vol
    return True


def verdict(oi_prev, oi_next, volume, *, expiry, session_date,
            opened_ratio, closed_ratio):
    """``(code, ratio)`` for a flagged contract's next-day open interest.

    ratio = (next - previous) / that day's volume. ``opened`` at or above
    ``opened_ratio``, ``closed`` at or below ``closed_ratio``, else ``mixed``.
    ``expired`` when the contract expired on the alert day; ``none`` (ratio
    None) for any unusable input -- never a guessed verdict."""
    if expiry and session_date and str(expiry) <= str(session_date):
        return "expired", None
    p, n, v = _finite(oi_prev), _finite(oi_next), _finite(volume)
    if p is None or n is None or v is None or v <= 0 or p < 0 or n < 0:
        return "none", None
    ratio = (n - p) / v
    if ratio >= opened_ratio:
        return "opened", ratio
    if ratio <= closed_ratio:
        return "closed", ratio
    return "mixed", ratio
```

`hiro._contracts` is private to `hiro`. Importing it is deliberate (one chain
walker, not two); add a one-line comment at `hiro._contracts` naming this reader.

**Step 4:** `"$PY" -m pytest services/options_svc/tests/test_flow_sides.py -q` → PASS.
Also run `shared/tests/test_numeric.py`.

**Step 5: Commit** `feat(options): pure bought/sold tally and open-interest verdict`.

---

### Task 5: The store — `flow_contract_days`

**Files:**
- Modify: `options-scanner/gex_history_db.py`
- Create: `options-scanner/tests/test_gex_history_flow_days.py` (model it on
  `test_gex_history_hiro.py`, including how that file gets a connection)

**Step 1: Failing tests.**

- `init_schema` creates the table; calling it twice is harmless.
- `upsert_flow_contract_days` inserts, and a second upsert of the same
  `(session_date, alert_id)` REPLACES the tallies, volume and `oi_prev` but never
  clears `oi_next` / `verdict`.
- a row with a non-finite `poll_*` value raises before anything is written.
- `load_flow_contract_days(conn, "2026-10-02")` returns that date's rows as dicts,
  ordered by `fired_ts`.
- `load_unresolved_flow_days(conn, before="2026-10-05")` returns rows with a NULL
  verdict and an earlier date, and no others.
- `resolve_flow_contract_day` sets `oi_next`, `oi_next_date`, `verdict`,
  `oi_ratio`; a second call overwrites them (the re-read).
- `purge_flow_contract_days(conn, keep_sessions=2)` keeps the newest two distinct
  session dates.
- `latest_flow_session_before(conn, "2026-10-05")` returns `"2026-10-02"`.

**Step 2:** Run → FAIL.

**Step 3: Implement.** Beside `HIRO_SCHEMA_SQL`:

```python
# Flagged flow-alert contracts, one row per alert per session
# (services/options_svc/flow_sides_tick.py). Its own retention.
FLOW_DAY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS flow_contract_days (
    session_date      TEXT    NOT NULL,
    alert_id          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    osi               TEXT    NOT NULL,
    side              TEXT,
    strike            REAL,
    expiry            TEXT,
    alert_type        TEXT,
    fired_ts          INTEGER,
    oi_prev           REAL,
    volume            REAL,
    poll_bought       REAL    NOT NULL,
    poll_sold         REAL    NOT NULL,
    poll_unlabelled   REAL    NOT NULL,
    stream_bought     REAL,
    stream_sold       REAL,
    stream_unlabelled REAL,
    oi_next           REAL,
    oi_next_date      TEXT,
    verdict           TEXT,
    oi_ratio          REAL,
    PRIMARY KEY (session_date, alert_id)
);
"""
```

Add `init_flow_day_schema(conn)` and call it from `init_schema` after
`init_hiro_schema`. Implement the six functions the tests name. The upsert is
`INSERT … ON CONFLICT(session_date, alert_id) DO UPDATE SET` over the tally,
volume and `oi_prev` columns only. Validate finiteness the way `_hiro_params`
does. The session date is a stored TEXT column here, so no `localtime` arithmetic
is needed.

**Step 4:** options-scanner suite passes.

**Step 5: Commit** `feat(scanner): flow_contract_days store`.

---

### Task 6: Session state, persistence, resolution, publishing — `flow_sides_tick.py`

This is the one stateful module. Everything it calls is already tested; its own
tests drive it with a fake bus and a temp database.

**Files:**
- Create: `services/options_svc/flow_sides_tick.py`
- Create: `services/options_svc/tests/test_flow_sides_tick.py`
- Modify: `services/options_svc/compute.py` (two touches, +1 line net)
- Modify: `services/options_svc/handlers.py` (`run_flow_alerts`, two cache names)

**Public surface**

```python
CACHE_SIDES = "cache:options:flow_sides"
EVENT_SIDES = "events:options:flow_sides"
CACHE_FOLLOWUP = "cache:options:flow_followup"
EVENT_FOLLOWUP = "events:options:flow_followup"
MAX_GAP_SEC = 450          # 1.5 x collection_tiers.MAX_TAIL_INTERVAL_MIN: the slowest
                           # tier's normal step must still be labelled

def reset() -> None                      # test helper: drop all state
def on_chain(symbol, chain, now=None) -> None        # collector thread; never raises
def after_alerts(bus, fresh, today, now_ts) -> None  # same thread; never raises
def wanted_osis() -> list                 # stream thread; a locked snapshot
def stream_tick(tick, label=True) -> None # stream thread
```

**State** (module level): one dict for the collector thread — `date`, `books`
`{symbol: book}`, `seeded` (set), `last_ts` `{symbol: ts}`, `flagged`
`{alert_id: row}`, `pending` `{symbol: {osi: [row, …]}}`, `followup_date`,
`dirty_followup` — and, under ONE `threading.Lock`, the stream thread's
`stream_book`, `stream_quotes` and `wanted` list.

**Behaviour, each line a test**

`on_chain`:
1. Does nothing when `[sides].enabled` is not literally `True`, or for a chain
   with no expiration map (an error body must not count as a first poll). It
   books volume for the whole collection window; only the open-interest read is
   limited to `market_calendar.is_regular_hours(now)` (index open interest reads
   zero off-hours, and that must stay out of `oi_prev` and the verdict).
2. A new CT session date clears the books, `seeded`, `last_ts`, `flagged` and the
   stream state. **`on_chain` never opens the database**; the reload from the
   store (see "restore" below) happens in `after_alerts`.
3. `label = False` when the symbol's last good minute is more than `MAX_GAP_SEC`
   old; otherwise `True`. Calls `flow_sides.advance(chain, book, seeded=…,
   label=…)`, then marks the symbol seeded and stamps `last_ts`.
4. Pending contracts are passed to `advance` as `watch`, so one that does not
   trade today still gets an entry and its open interest is read. The verdict is
   derived in `after_alerts` from the book: if the row has no `oi_next`, or the
   figure differs from the one stored, record it, derive `flow_sides.verdict(...)`
   from the `[followup]` ratios, queue the store write, and log at INFO when a
   stored figure MOVED (that log is the measurement the design asks for).
5. Any exception → `_degrade.degraded("options.flow_sides.on_chain", detail=symbol)`.

`after_alerts(bus, fresh, today, now_ts)`:
1. Registers each `fresh` alert whose `type` is `uoa` or `big_delta` and whose
   `osi` is truthy, keyed by its `id`: symbol, osi, side, strike, expiry, type,
   `fired_ts`. Appends the osi to `wanted` unless `[sides].stream` is off or the
   list is at `stream_max_contracts`.
2. Builds a row per flagged alert from the poll book entry (`tally`, `HW` as
   volume, `OI` as `oi_prev`) and the stream book entry when one exists, and
   upserts the rows whose values changed since the last write.
3. Writes queued resolutions.
4. Publishes `CACHE_SIDES` =
   `{"date": today, "public": <bool>, "contracts": {alert_id: {"poll": {…},
   "stream": {…} or None, "volume": v}}}` with `skip_unchanged=True`. It carries
   no timestamp. Publishes nothing when there are no flagged contracts.
5. When `dirty_followup` (or on the first call of a session), publishes
   `CACHE_FOLLOWUP` = `{"date": <previous session date>, "public": <bool>,
   "rows": [ …store rows as dicts… ]}`.
6. Purges with `[followup].keep_sessions` once per session date.
7. Opens its own write connection (`gex_history_db.connect()`), calls
   `init_flow_day_schema` once per process, and closes it in `finally`.
8. `"public"` is `True` only when `[sides].public` is literally `True`.
9. Any exception → `_degrade.degraded("options.flow_sides.after_alerts")`.

Restore (on the first `after_alerts` of a session, or after a restart):
- `load_unresolved_flow_days(before=today)`: a row whose `expiry` is on or before
  its `session_date` is resolved at once as `expired`; a row whose `expiry` is
  before today but after its session date is resolved as `none`; the rest go to
  `pending`.
- `load_flow_contract_days(today)`: each row re-enters `flagged`, its osi re-enters
  `wanted`, and its stored tally seeds the poll book entry with `HW` = the stored
  volume. The symbol is NOT marked seeded, so volume printed while the service was
  down books as unlabelled on the next poll.

**Step 1: Failing tests** for every numbered line above. Use `tmp_path` for the
database (monkeypatch `gex_history_db.DB_PATH`, as `test_gex_history_hiro.py`
does), the fake bus, and an explicit `now`. Three tests matter most:

```python
def test_alert_reports_volume_from_before_it_fired(...):
    # Minute 1 seeds, minute 2 books 400 at the ask, then the alert fires.
    # The published contract must show bought == 400, not 0.

def test_restart_books_missed_volume_as_unlabelled(...):
    # Flag a contract at volume 1000 (bought 400 / unlabelled 600), reset(),
    # poll again at volume 1500: unlabelled == 1100, bought == 400.

def test_followup_resolves_from_the_next_sessions_chain(...):
    # Friday row: oi_prev 1000, volume 1000, expiry the following Friday.
    # Monday chain shows openInterest 1700 -> verdict "opened", ratio 0.7,
    # and cache:options:flow_followup carries it.
```

And two that guard the public switch and config:

```python
def test_public_flag_follows_config(...)      # monkeypatch the accessor
def test_disabled_switch_measures_nothing(...) # [sides].enabled = "true" (a string) is OFF
```

**Step 2:** Run → FAIL.

**Step 3: Implement** the module. Read config through
`flow_alerts.load_thresholds()` + `flow_alerts.section(cfg, "sides" | "followup")`
on each call (it is mtime-cached). Coerce the two ratios and the two integers
with `shared.numeric.finite`, falling back to `flow_alerts._DEFAULTS`. Import the
scheduler clock lazily, as `compute` does, to avoid the import cycle.

**Step 4: Wire it in.**

`compute.py`, in `collect_gex_snapshots`:
- change `from services.options_svc import eth` to
  `from services.options_svc import eth, flow_sides_tick` (no new line);
- in `on_chain`, directly after the `_eth_seen[sym] = …` line, add
  `flow_sides_tick.on_chain(sym, chain, now)` (**the one new line**).

Then set `COMPUTE_MAX_LINES` in `test_compute_module_shape.py` only if the test
asks for it; never raise it.

`handlers.py`, in `run_flow_alerts`, after the `big_delta` stash loop and before
`if fresh:`:

```python
        # Bought/sold tally + next-day open interest. Never raises.
        flow_sides_tick.after_alerts(bus, fresh, today, now_ts)
```

and import `flow_sides_tick` beside `flow_alerts` at the top.

Add a wiring test in `test_flow_sides_tick.py` that reads `compute.py`'s source
and asserts the `flow_sides_tick.on_chain(` call sits inside `def on_chain`, so a
later refactor cannot silently drop it.

**Step 5:** Options suite passes, including `test_compute_module_shape.py` and
`tests/test_no_silent_degrades.py` (run that one from the repo root).

**Step 6: Commit** `feat(options): flow-alert bought/sold tally, store and views`.

---

### Task 7: Stream a contract after its alert

**Files:**
- Create: `services/_sse.py`
- Create: `services/options_svc/flow_stream.py`
- Create: `services/options_svc/tests/test_flow_stream.py`
- Modify: `services/options_svc/scheduler.py` (`loop`)

**Step 1: Failing tests.**

`services/_sse.py` — `parse_sse_line(line)` and `reconnect_delay(failures, *,
base, cap)`. Copy the test cases from
`services/sentiment_svc/tests/test_order_flow_consumer.py`. (The sentiment and
portfolio services each hold a private copy; migrating them is NOT part of this
plan. Note it in the module docstring.)

`flow_stream.py` pure helpers:

```python
def test_stream_url_params_join_osis_unchanged():
    # OSIs are case- and space-sensitive: "SPY   261030C00787000" must survive.

def test_set_changed_detects_growth_only():
    # same members in another order -> False; a new member -> True

def test_first_tick_after_connect_is_unlabelled(...):
    # handle(tick, seen) passes label=False for an osi not yet in `seen`,
    # label=True afterwards.
```

**Step 2:** Run → FAIL.

**Step 3: Implement.** `flow_stream.py` mirrors
`sentiment_svc/order_flow_consumer._option_stream_worker`:

- `_worker(stop)`: while not stopped, read `flow_sides_tick.wanted_osis()`. Empty
  → `stop.wait(poll_sec)` and retry. Otherwise open
  `GET {PROXY_URL}/stream/options?symbols=…` with `stream=True,
  timeout=(10, None)`; for each line, re-read the wanted list at most every
  `_RECHECK_SEC` and `break` to reconnect when it grew; parse; call
  `flow_sides_tick.stream_tick(tick, label=osi in seen)` and add the osi to
  `seen`. `seen` is reset on every connect, so the volume that printed across a
  reconnect is unlabelled.
- Capped exponential backoff on failure; a WARNING per failed attempt; never
  raises out.
- `start() -> threading.Event` launches the daemon thread named
  `options-flow-stream` and returns its stop event.
- The two cadences (`poll_sec`, `_RECHECK_SEC`) follow the poll, not an
  operator's choice: module constants with a comment saying so.

**Step 4: Start it.** In `scheduler.loop`, after the startup buying-power
reconcile block:

```python
    # Stream flagged flow-alert contracts (bought/sold after the alert).
    # Best-effort: a failure here must never stop the scheduler.
    try:
        flow_stream.start()
    except Exception:
        log.exception("flow stream failed to start")
```

The scheduler loop itself only runs when `_scaffold._schedulers_enabled()`, so the
dev profile and pytest never start the thread. Add a test that asserts
`flow_stream.start` is referenced from `scheduler.loop`'s source.

**Step 5:** Options suite passes.

**Step 6: Commit** `feat(options): stream flagged flow-alert contracts`.

---

### Task 8: The Flow Alerts page

**Files:**
- Modify: `webgui/pages/options/flow.py`
- Modify: `webgui/pages/copy.py` (the shared sentences)
- Test: `webgui/tests/test_flow_page.py`
- Check: `webgui/tests/test_bus_client.py`, `test_no_inline_style.py`,
  `test_ui_kit_guard.py`

**Step 1: Failing tests** for four new pure functions in `flow.py`:

```python
def test_shares_from_a_tally():
    assert flow.shares({"bought": 620, "sold": 300, "unlabelled": 80}) == {
        "bought": 0.62, "sold": 0.30, "unlabelled": 0.08, "volume": 1000.0}

@pytest.mark.parametrize("bad", [None, {}, {"bought": 1}, 
    {"bought": float("nan"), "sold": 1, "unlabelled": 1},
    {"bought": -1, "sold": 1, "unlabelled": 1},
    {"bought": 0, "sold": 0, "unlabelled": 0}])
def test_shares_of_an_unusable_tally_is_none(bad):
    assert flow.shares(bad) is None

def test_sides_text_names_all_three_shares():
    t = {"poll": {"bought": 620, "sold": 300, "unlabelled": 80}, "stream": None}
    assert flow.sides_text(t) == "≈ bought 62.00% · sold 30.00% · unlabelled 8.00%"

def test_sides_text_adds_the_stream_figure_when_it_has_volume():
    t = {"poll": {"bought": 620, "sold": 300, "unlabelled": 80},
         "stream": {"bought": 710, "sold": 200, "unlabelled": 90}}
    assert flow.sides_text(t).endswith(
        " · since the alert: bought 71.00% of 1,000")

def test_sides_text_is_empty_without_a_tally():
    assert flow.sides_text(None) == ""
```

- `alert_rows(view, sides=None)`: each row gains `"sides"` (the text; `""` when
  the alert has no entry, when `sides["date"]` is not the alert view's date, or
  for a non-contract alert kind). Existing callers that pass one argument must
  keep working, and every existing `alert_rows` test must pass unchanged.
- `sides_view_shown(sides)`: returns `None` while `_hiding()` and
  `sides.get("public") is not True`; otherwise the view. Test both branches by
  monkeypatching `flow._hiding`.
- `followup_rows(view)`: display rows for the Previous session panel: contract
  (`_exp_short` + strike + C/P), symbol, volume, the session share text, open
  interest before → after, and the verdict in whole words — "Mostly opened",
  "Mostly closed", "Mixed, or traded within the day", "Expired — no reading",
  "No reading", and "Waiting for today's open interest" while unresolved. Total
  over a malformed view.

Percentages go through `pages/fmt.pct`, which takes a value ALREADY in percent
units: pass `share * 100`. Counts use the existing comma format.

**Step 2:** Run → FAIL.

**Step 3: Implement** the functions, then the page:

- Add `SIDES_VIEW = "options:flow_sides"` and `FOLLOWUP_VIEW =
  "options:flow_followup"`.
- Add a "Bought / sold (estimate)" column to `flow_columns()`.
- The poll must repaint when EITHER `VIEW` or `SIDES_VIEW` moves: probe both
  versions with one `bus_client.read_versions`, read payloads off the event loop,
  and call `_shown` / `sides_view_shown` on the event loop (see the warning in
  `_shown`'s docstring).
- Under the table, a "Previous session" panel built with `kit.table`, shown only
  when `followup_rows` is non-empty, with one caption line: what the verdict is,
  that it is an estimate, and that a same-day expiry can never have one.
- All new user-facing sentences that more than one screen shows go in
  `pages/copy.py`.

**Step 4:** Webgui suite passes. Check `test_bus_client.py`'s pinned reader set
and add the two views where a list requires them.

**Step 5: Verify in the harness.** `tools/ui_harness.py` on the Flow Alerts page
with a fake bus seeded with: the alert view, a sides view (one alert with a
stream figure, one without, one absent), and a follow-up view with one row per
verdict. Confirm the port is free first. Screenshot private and public
(`sides.public` true, then false: the column must be blank and the panel gone on
the public origin). Stop the harness.

**Step 6: Commit** `feat(webgui): bought/sold estimate and previous-session panel on Flow Alerts`.

---

### Task 9: The Desk

**Files:**
- Modify: `webgui/pages/desk.py`
- Test: `webgui/tests/test_desk.py`, `webgui/tests/test_render_size.py`

**Step 1: Failing tests.**

- `desk.flow_rows(flow_view, sides_view)` passes the sides view through to
  `flow.alert_rows`; called with one argument it behaves as today.
- `"options:flow_sides"` is in `desk.VIEWS` and in `_REGION_VIEWS["flow"]`.
- A row with `"sides"` renders a second line; a row without renders exactly as
  today (assert on the element structure the existing `_flow_row` tests use).

**Step 2:** Run → FAIL.

**Step 3: Implement.**

- `flow_rows` gains the optional second argument.
- `_flow_row`: the "what traded" cell becomes a `_stack()` of the existing detail
  label and, only when `row["sides"]` is non-empty, a second
  `text-[10px] min-w-0 truncate` label carrying `row["sides"]`. Nothing is added
  for a row with no estimate, so those rows keep their height.
- Wire the view into the flow region's paint. The Desk reads views through the
  one batched `read_versions`; find where the flow region's payload is handed to
  `flow_rows` and pass the sides payload beside it, filtered through
  `flow.sides_view_shown` **on the event loop**.
- If `desk.render` grows past its ceiling, move wiring into a module-level
  helper. Do not raise the ceiling.
- Update the docstring of `flow_rows`: the rows now DO carry a bought/sold
  figure, an estimate, and the Desk adds no stronger claim.

**Step 4:** Webgui suite passes.

**Step 5: Verify in the harness** on the Desk: a mix of rows with and without an
estimate. Check the flow panel's row heights, the pinned columns, and that
nothing overflows the panel at the narrow width the existing tests use.

**Step 6: Commit** `feat(webgui): bought/sold estimate on the Desk's flow rows`.

---

### Task 10: Documentation

Same commit series, not a later one.

- `webgui/page_help.py`: `/desk` (the Flow alerts bullet currently says "never
  *bought* or *sold*" — rewrite it: the row now shows an estimate, what
  "unlabelled" means, and that it is not a tape) and `/options/flow` (the new
  column, the Previous session panel, why most rows never get a verdict).
  `webgui/tests/test_page_help.py` must pass.
- `docs/manuals/`: User Guide (how to read the estimate and the verdict),
  Technical Reference (the labelling rule, the verdict formula and its two
  ratios, the 87% same-day-expiry measurement), API Reference (the two cache
  views, the `osi` field on alerts, `total_volume` on the option stream tick, the
  `flow_contract_days` table), Options Glossary ("open interest change",
  "bought to open" only if the screens use the phrase — they do not; skip it).
  Rebuild with `build_docs.py`.
- `docs/webgui-routes.md`: `/options/flow` and `/desk`.
- `docs/reference/config-files.md`: the two new tables.
- `docs/CHANGELOG.md`: a dated entry.
- Root `CLAUDE.md`: no entry unless an invariant changed. One did: add a single
  sentence under "Options, paper books and trade selection → Other standing
  facts": the flow-alert bought/sold figure is an estimate whose tally must
  always sum to the contract's volume, and unwatched volume is unlabelled.
  `tests/test_claude_md_size.py` must pass.

**Commit** `docs: bought/sold on flow alerts — help, manuals, changelog`.

---

### Task 11: Verify the whole change

Use @superpowers:verification-before-completion.

1. Run all five suites. The failing and skipped SETS must equal Task 0's.
2. `tools/git-hooks/pre-commit` lint gate is clean; `pyright` (the narrow config)
   is clean.
3. `"$PY" -m pytest tests -q` from the repo root (the cross-cutting guards:
   CI coverage, CLAUDE.md size, silent degrades).
4. Re-read the diff of `compute.py`: exactly one added line, one edited line.
5. Request review with @superpowers:requesting-code-review. The reviewer must be
   told to judge every changed assertion in existing tests.

### After merge and promote (the user decides when)

There is no dev environment, so the service is verified on prod during a session.
Read-only checks, by Redis and SQLite, not by restarting anything:

1. `cache:options:flow_sides` has today's date, and for a flagged contract
   bought + sold + unlabelled equals `volume`.
2. A contract flagged after 09:00 CT shows a non-zero poll tally from before its
   `fired_ts`, and its stream tally starts at zero and grows.
3. The proxy log shows one option SSE subscriber from the options service, and
   paper-trade leg quotes still update.
4. `/health` on the options service: `degrades_total` has no
   `options.flow_sides.*` entries.
5. **The open measurement.** On the second session, read the journal for the
   "open interest moved" INFO lines. If any appear, note the time of day: that is
   when Schwab's chain picked up the new figure, and the design doc's warning gets
   replaced by the measured answer.
6. The share of the first session's volume that came out unlabelled, per source.
   Report it to the user as measured, whatever it is.
