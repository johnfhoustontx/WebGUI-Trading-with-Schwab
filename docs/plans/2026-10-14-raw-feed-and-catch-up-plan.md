# Raw Feed and Catch-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An independent ingest (`feed_svc`) writes every one-minute option chain to a per-session raw store; options_svc derives GEX, flow and HIRO from that store behind a cursor, so a stopped, crashed or redeployed options_svc catches up with no gap, and a market-hours promote restarts only the app while the proxy and the ingest keep running.

**Architecture:** First split `gex_collector.poll_once` into a FETCH half that produces a `RawMinute` and a DERIVE half that consumes one, with no behaviour change. Then add the raw store and a `tee` mode (today's collector also writes raw; a shadow deriver proves derive-from-raw is identical and measures its cost). Then the cursor-driven deriver, then `feed_svc`, then the two systemd targets and the promote that leaves the feed running. Each phase ships on its own; `config/feed.toml [feed] mode` (`off` → `tee` → `on`) gates every behaviour change.

**Tech Stack:** Python 3.11, SQLite (stdlib `sqlite3`, WAL), `zlib` + `json`, FastAPI via `services/_scaffold.make_app`, systemd user units via `deploy/systemd/generate_units.py`, bash (`tools/promote.sh`), pytest with the repo-root conftest guards.

**Spec:** [`docs/plans/2026-10-14-raw-feed-and-catch-up-design.md`](2026-10-14-raw-feed-and-catch-up-design.md) — read it first; this plan argues from it.

## Global Constraints

- **Venv:** `$PY` below is the main checkout's interpreter: `D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe` on the Windows box (a worktree has no venv of its own).
- **Test locations:** `options-scanner` tests run from INSIDE that folder (`cd options-scanner && $PY -m pytest tests/... -q`); each service suite runs from the repo root on its own (`$PY -m pytest services/options_svc -q`); never `pytest services` over several.
- **No live store, no network in tests:** the root conftest guards `sqlite3.connect` and the HTTP stacks. Every new store takes `db_path=None` / `root=None` resolved AT CALL TIME so a test can point it at `tmp_path`.
- **Configurable by default:** every tunable is in `config/feed.toml`, read through `shared.config_toml.toml_loader`, with an entry in `webgui/config_schema.py` (`webgui/tests/test_config_schema.py` fails otherwise). Values from the spec, verbatim: `[feed] mode = "off"`, `keep_sessions = 5`, `stale_alarm_min = 2`; `[catchup] push_max_age_min = 3`, `lag_alarm_min = 5`; `config/ports.toml [services] feed = 8218`.
- **A swallowed exception leaves a trace:** a guard of 15+ lines calls `_degrade.degraded("<area>")` (`test_no_silent_degrades.py`).
- **`options_svc/compute.py` must not grow** (a ceiling test): new service code goes in sibling modules that import nothing from `compute`.
- **A timezone-naive datetime means Central.** A minute is the minute-floored unix second (`ts`), identical in the raw store and `gex_history.db`.
- **`mode = "off"` is today's behaviour, byte for byte.** Every existing suite stays green at every commit.
- **Never `git add -A`** (another session shares this tree): stage the exact files each task names. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Do not weaken a test to make it pass.** A failing existing test after a refactor is a behaviour change: fix the code.

## Review Focus

1. **A carried chain on replay.** With tiers on, a carried symbol's Greek rows come from the carried chain and its skew/premium from the chain AS STORED, and it is never handed to `on_chain`. The raw minute must keep both chains and the carried flag, or a replay writes different rows. → Task 1 (round-trip test with a carried symbol) and Task 3 (store keeps `stored` + `carried`).
2. **Wall-clock leakage during catch-up.** `flow_sides_tick.on_chain`, `traded_premium.on_chain`, HIRO's minute and `run_flow_alerts` all read "now". Replaying 10:42 at 13:00 must stamp 10:42, keep the flow window open for 10:42, and not push. → Task 2 (hooks get the minute's datetime), Task 8 (`as_of` + push gate).
3. **A crash between rows and cursor.** If the cursor moves without the rows (or the reverse), a minute is skipped or doubled. → Task 7 (one transaction; a test kills it between the two writes).
4. **The proxy and the ingest stay up while the code under them changes.** `git merge --ff-only` swaps files under running processes; a lazy import in the proxy or `feed_svc` would load NEW code beside OLD. Safe only because the feed's file set is unchanged — which is exactly what `tools/feed_files.py` must compute including function-level imports. → Task 14 (closure test includes a lazy import).
5. **The port wait in `promote.sh`.** `stop_stack` waits until NO `8xxx` port listens; with the proxy (8100) and `feed_svc` (8218) still up it would wait its full 15 s and then carry on regardless, hiding a unit that did not stop. → Task 14 (wait only for the app's own ports, tested through the helper).

---

## Phase 1 — Split fetch from derive (no behaviour change)

### Task 1: `RawMinute` and the split of `gex_collector.poll_once`

**Files:**
- Create: `shared/raw_minute.py`
- Modify: `options-scanner/gex_collector.py` (`poll_once` ~554–835, `poll_term_once` ~854)
- Test: `options-scanner/tests/test_gex_collector_split.py`

**Interfaces:**
- Produces:
  ```python
  # shared/raw_minute.py  (stdlib only)
  @dataclass
  class RawMinute:
      ts: int                                   # minute-floored unix seconds
      chains: list[tuple[str, dict]]            # (symbol, chain) in fetch order, AFTER re-anchor and carry
      carried: set[str] = field(default_factory=set)
      carried_age: dict[str, float | None] = field(default_factory=dict)
      stored: dict[str, dict] = field(default_factory=dict)   # as-stored chain, ONLY where it differs from `chains`
      term: dict | None = None                  # the 10-day $SPX term chain, when polled this minute
      symbols: list[str] = field(default_factory=list)        # the universe asked this minute
      def chain_for_skew(self, symbol: str) -> dict | None: ...   # stored.get(symbol) or the chain
  ```
  ```python
  # gex_collector.py
  def fetch_minute(client, symbols=None, now=None, tiers=None, lock=None, poll_term=None) -> RawMinute
  def derive_minute(engine, conn, minute: RawMinute, on_chain=None) -> None   # writes rows, does NOT commit
  def fetch_term_chain(client, today, lock=None) -> dict | None
  def derive_term(engine, conn, chain: dict, ts_iso: str) -> None
  def poll_once(...)   # unchanged signature: derive_minute(fetch_minute(...)); conn.commit()
  ```

- [ ] **Step 1: Write the failing test** — `options-scanner/tests/test_gex_collector_split.py`. Reuse the `_client` / `_chain` doubles from `test_gex_collector_tiers.py` (import them), and a recording engine:

```python
"""poll_once == derive_minute(fetch_minute(...)), and the RawMinute carries
everything derive needs (carried chains included)."""
import datetime as dt
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import gex_collector as gc
from tests.test_gex_collector_tiers import _client, TIERS
from shared.raw_minute import RawMinute

CT = ZoneInfo("America/Chicago")
RTH = dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)


class _Engine:
    def __init__(self):
        self._last_dte = 0
        self.seen = []

    def calc_all_from_chain(self, chain, use_volume=False):
        self.seen.append((chain["symbol"], chain.get("underlyingPrice")))
        return (None, None, None, None)


def test_fetch_minute_returns_a_raw_minute_with_the_snapped_ts():
    m = gc.fetch_minute(_client(), symbols=["SPY", "QQQ"], now=RTH + dt.timedelta(seconds=37),
                        poll_term=False)
    assert isinstance(m, RawMinute)
    assert m.ts == int(RTH.timestamp())
    assert [s for s, _ in m.chains] == ["SPY", "QQQ"]
    assert m.term is None and m.carried == set()


def test_poll_once_is_fetch_then_derive():
    a, b = _Engine(), _Engine()
    gc.poll_once(_client(), a, MagicMock(), symbols=["SPY", "QQQ"], poll_term=False, now=RTH)
    m = gc.fetch_minute(_client(), symbols=["SPY", "QQQ"], now=RTH, poll_term=False)
    gc.derive_minute(b, MagicMock(), m)
    assert a.seen == b.seen


def test_a_carried_symbol_is_marked_and_not_handed_to_on_chain():
    # AAPL is in the tail and not due this minute -> carried (see test_gex_collector_tiers._not_due)
    from tests.test_gex_collector_tiers import _not_due
    now = _not_due("AAPL")
    m = gc.fetch_minute(_client(ages={"AAPL": 90.0}), symbols=["SPY", "AAPL"], now=now,
                        tiers=TIERS, poll_term=False)
    assert "AAPL" in m.carried and m.carried_age["AAPL"] == 90.0
    handed = []
    gc.derive_minute(_Engine(), MagicMock(), m, on_chain=lambda s, c: handed.append(s))
    assert handed == ["SPY"]
```

- [ ] **Step 2: Run it — expect FAIL** (`ModuleNotFoundError: shared.raw_minute` / `AttributeError: fetch_minute`).
  Run: `cd options-scanner && $PY -m pytest tests/test_gex_collector_split.py -q`

- [ ] **Step 3: Implement.** Create `shared/raw_minute.py` with the dataclass above (`chain_for_skew` returns `self.stored.get(symbol)` if present, else the chain from `chains`). In `gex_collector.py`:
  - Move lines from the top of `poll_once` through the carry block (`_NOTHING_LISTED` bookkeeping, `_fetch`, the pool, `_reanchor_spots`, `_carry_forward`) into `fetch_minute`, which returns `RawMinute(ts=ts_boundary, chains=[(s, c) for s, c in fetched if c], carried=carried, carried_age={s: ages.get(s) for s in carried}, stored={s: as_stored[s] for s in carried if s in as_stored}, term=..., symbols=list(symbols))`. The term chain: `do_term` as today; when true, `term = fetch_term_chain(client, today, lock)`.
  - Move the per-symbol loop (`on_chain` call, skew fields, engine pass, the five `insert_snapshot` calls) into `derive_minute`, reading `minute.chains`, `minute.carried`, `minute.carried_age` and `minute.chain_for_skew(symbol)` where the old code read `as_stored.get(symbol, chain)`. When `minute.term` is set, call `derive_term(engine, conn, minute.term, ts_iso=datetime.fromtimestamp(minute.ts, TZ).isoformat())`. No `conn.commit()` inside.
  - Split `poll_term_once` the same way: `fetch_term_chain` (the `get_option_chain` call, `None` on non-200) and `derive_term` (everything after); `poll_term_once` keeps its signature and composes them.
  - `poll_once` becomes: `derive_minute(engine, conn, fetch_minute(client, symbols, now, tiers, lock, poll_term), on_chain); conn.commit()`.

- [ ] **Step 4: Run the new test and the whole options-scanner suite — expect PASS, failing set empty.**
  Run: `cd options-scanner && $PY -m pytest tests -q -rf`

- [ ] **Step 5: Commit**
  ```bash
  git add shared/raw_minute.py options-scanner/gex_collector.py options-scanner/tests/test_gex_collector_split.py
  git commit -m "refactor(gex): split the collector poll into fetch_minute and derive_minute"
  ```

### Task 2: The options-service collection becomes fetch + derive, hooks on the minute's clock

**Files:**
- Create: `services/options_svc/collect_derive.py`
- Modify: `services/options_svc/compute.py` (`collect_gex_snapshots` ~4838–5016: body moves out; `compute` keeps a thin wrapper)
- Test: `services/options_svc/tests/test_collect_derive.py`

**Interfaces:**
- Consumes: `gex_collector.fetch_minute`, `derive_minute` (Task 1), `RawMinute`.
- Produces:
  ```python
  # collect_derive.py  (imports nothing from compute)
  @dataclass
  class DeriveContext:            # what today's collect_gex_snapshots builds before poll_once
      on_chain: Callable[[str, dict], None]
      hiro_rows: dict
      eth_seen: dict
      minute_dt: datetime         # tz-aware CT datetime of the minute
  def build_context(minute_ts: int, capture: set | None) -> DeriveContext
  def derive_into(conn, minute: RawMinute, ctx: DeriveContext, engine=None) -> None
      # derive_minute + _write_hiro_rows + traded_premium.write_rows, NO commit
  def finish(ctx: DeriveContext) -> None   # _publish_eth_eligibility(ctx.eth_seen)
  ```

- [ ] **Step 1: Write the failing test**

```python
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import collect_derive, flow_sides_tick, traded_premium
from shared.raw_minute import RawMinute

CT = ZoneInfo("America/Chicago")


def test_hooks_receive_the_minutes_clock_not_the_wall_clock(monkeypatch):
    seen = []
    monkeypatch.setattr(flow_sides_tick, "on_chain", lambda s, c, now: seen.append(now))
    monkeypatch.setattr(traded_premium, "on_chain", lambda s, c, now: seen.append(now))
    minute = int(dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT).timestamp())
    ctx = collect_derive.build_context(minute, capture=set())
    ctx.on_chain("SPY", {"symbol": "SPY", "underlyingPrice": 600.0,
                         "callExpDateMap": {}, "putExpDateMap": {}})
    assert seen and all(n == dt.datetime.fromtimestamp(minute, CT) for n in seen)


def test_hiro_is_stamped_with_the_minute(monkeypatch):
    minute = int(dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT).timestamp())
    ctx = collect_derive.build_context(minute, capture=set())
    assert ctx.minute_dt.hour == 10 and ctx.minute_dt.minute == 42
```

- [ ] **Step 2: Run it — expect FAIL** (`ModuleNotFoundError`). Run: `$PY -m pytest services/options_svc/tests/test_collect_derive.py -q`

- [ ] **Step 3: Implement.** Move the body of `collect_gex_snapshots` from `clear_uoa_stash()` through the `on_chain` closure into `build_context(minute_ts, capture)`: every place that read `now` / `_hiro_now` now reads `ctx.minute_dt = datetime.fromtimestamp(minute_ts, CT)`; `flow_sides_tick.on_chain(sym, chain, ctx.minute_dt)`, `traded_premium.on_chain(sym, chain, ctx.minute_dt)`; `_hiro_ts = minute_ts`. The stash helpers (`stash_uoa`, `_stash_tick_chain`, `hiro_tick_row`, …) still live in `compute`, and `collect_derive` may not import `compute` (the ceiling rule) — so move the small stash helpers they need into `collect_derive.py` and leave `compute` re-exporting them (`from services.options_svc.collect_derive import stash_uoa, ...  # noqa: F401`) so existing tests that patch `compute.*` keep working; run the suite to find any that patch a moved name and patch the re-export instead. `compute.collect_gex_snapshots(capture_symbols=None, now=None)` keeps its signature and becomes: plan symbols (`_gth_symbols`), the lock, schema/purge as today, `minute = gc.fetch_minute(...)`, `ctx = collect_derive.build_context(minute.ts, capture_symbols)`, `collect_derive.derive_into(conn, minute, ctx)`, `conn.commit()`, `touch_lock`, `collect_derive.finish(ctx)`. `_write_hiro_rows` and `traded_premium.write_rows`: if either calls `conn.commit()`, remove that call (the caller commits once).

- [ ] **Step 4: Run the new tests and the full options_svc suite — expect PASS, failing set empty.** Run: `$PY -m pytest services/options_svc -q -rf`

- [ ] **Step 5: Commit**
  ```bash
  git add services/options_svc/collect_derive.py services/options_svc/compute.py services/options_svc/tests/test_collect_derive.py
  git commit -m "refactor(options): collection is fetch_minute + derive_into; hooks run on the minute's clock"
  ```

---

## Phase 2 — The raw store and `tee`

### Task 3: The raw store

**Files:**
- Create: `shared/raw_store.py`
- Modify: `repo_paths.py` (add `RAW_DIR = ROOT / "services" / "feed_svc" / "data" / "raw"`), `.gitignore` (add `services/feed_svc/data/`), `tools/backup_local.py` (`db_paths` skips `RAW_DIR`)
- Test: `shared/tests/test_raw_store.py`, `tools/tests/test_backup_local.py` (one new test)

**Interfaces:**
- Consumes: `RawMinute` (Task 1).
- Produces (all take `root=None` → `repo_paths.RAW_DIR` at call time):
  ```python
  def session_path(date_iso: str, root=None) -> pathlib.Path          # <root>/<date>.db
  def write_minute(date_iso: str, minute: RawMinute, root=None) -> None   # one transaction; `minutes` row last
  def complete_minutes(date_iso: str, after_ts: int = -1, root=None) -> list[int]
  def last_complete(date_iso: str, root=None) -> int | None
  def read_minute(date_iso: str, ts: int, root=None) -> RawMinute | None
  def write_gap(date_iso: str, ts: int, reason: str, root=None) -> None
  def gaps(date_iso: str, root=None) -> list[tuple[int, str]]
  def sessions(root=None) -> list[str]                                   # date_iso, oldest first
  def prune(keep_sessions: int, root=None) -> list[str]                  # deleted dates
  ```
  Schema (this refines the spec's §4: the `kind` set is `chain` / `stored` / `term`, and an explicit `carried` flag — a carried chain's age can be unknown):
  ```sql
  CREATE TABLE IF NOT EXISTS chains (ts INTEGER NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL,
      carried INTEGER NOT NULL DEFAULT 0, carried_age_sec REAL, seq INTEGER NOT NULL,
      body BLOB NOT NULL, PRIMARY KEY (ts, symbol, kind));
  CREATE TABLE IF NOT EXISTS minutes (ts INTEGER PRIMARY KEY, symbols TEXT NOT NULL, written_at REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS gaps (ts INTEGER PRIMARY KEY, reason TEXT NOT NULL, noted_at REAL NOT NULL);
  ```
  `seq` preserves fetch order; `minutes.symbols` is the JSON list of the universe asked; `body` is `zlib.compress(json.dumps(chain, separators=(",", ":")).encode(), 6)`. Readers open `file:<path>?mode=ro` with `uri=True`; the writer sets `PRAGMA journal_mode=WAL`.

- [ ] **Step 1: Write the failing tests** — `shared/tests/test_raw_store.py`:

```python
import sqlite3

import pytest

from shared import raw_store as rs
from shared.raw_minute import RawMinute

D = "2026-10-14"


def _m(ts, carried=False):
    chains = [("SPY", {"symbol": "SPY", "underlyingPrice": 600.0}),
              ("AAPL", {"symbol": "AAPL", "underlyingPrice": 250.0})]
    return RawMinute(ts=ts, chains=chains,
                     carried={"AAPL"} if carried else set(),
                     carried_age={"AAPL": None} if carried else {},
                     stored={"AAPL": {"symbol": "AAPL", "underlyingPrice": 249.0}} if carried else {},
                     term={"symbol": "$SPX"} if ts % 300 == 0 else None,
                     symbols=["SPY", "AAPL"])


def test_round_trip_keeps_order_carry_stored_and_term(tmp_path):
    rs.write_minute(D, _m(1_760_000_100, carried=True), root=tmp_path)
    got = rs.read_minute(D, 1_760_000_100, root=tmp_path)
    assert [s for s, _ in got.chains] == ["SPY", "AAPL"]
    assert got.carried == {"AAPL"} and got.carried_age == {"AAPL": None}
    assert got.chain_for_skew("AAPL")["underlyingPrice"] == 249.0
    assert got.term is None and got.symbols == ["SPY", "AAPL"]


def test_a_minute_is_complete_only_once_its_minutes_row_is_written(tmp_path, monkeypatch):
    real = rs._insert_minute_row
    monkeypatch.setattr(rs, "_insert_minute_row", lambda *a: (_ for _ in ()).throw(RuntimeError("die")))
    with pytest.raises(RuntimeError):
        rs.write_minute(D, _m(1_760_000_160), root=tmp_path)
    monkeypatch.setattr(rs, "_insert_minute_row", real)
    assert rs.complete_minutes(D, root=tmp_path) == []
    # and the chains of the failed minute were rolled back with it
    conn = sqlite3.connect(rs.session_path(D, root=tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0] == 0


def test_complete_minutes_after_a_cursor(tmp_path):
    for ts in (60, 120, 180):
        rs.write_minute(D, _m(ts), root=tmp_path)
    assert rs.complete_minutes(D, after_ts=60, root=tmp_path) == [120, 180]
    assert rs.last_complete(D, root=tmp_path) == 180


def test_gaps_and_prune(tmp_path):
    rs.write_gap(D, 240, "feed down", root=tmp_path)
    assert rs.gaps(D, root=tmp_path) == [(240, "feed down")]
    for d in ("2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-13", D):
        rs.write_minute(d, _m(60), root=tmp_path)
    assert rs.prune(5, root=tmp_path) == ["2026-10-07"]
    assert rs.sessions(root=tmp_path)[0] == "2026-10-08"
```

  And in `tools/tests/test_backup_local.py`:

```python
def test_the_raw_feed_store_is_never_backed_up(tmp_path, monkeypatch):
    import repo_paths
    from tools import backup_local
    raw = tmp_path / "services" / "feed_svc" / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "2026-10-14.db").write_bytes(b"x" * 10)
    keep = tmp_path / "options-scanner" / "data"
    keep.mkdir(parents=True)
    (keep / "signals.db").write_bytes(b"x" * 10)
    monkeypatch.setattr(repo_paths, "RAW_DIR", raw)
    names = [p.name for p in backup_local.db_paths(tmp_path)]
    assert names == ["signals.db"]
```

- [ ] **Step 2: Run them — expect FAIL.** Run: `$PY -m pytest shared/tests/test_raw_store.py tools/tests/test_backup_local.py -q`
- [ ] **Step 3: Implement** `shared/raw_store.py` (stdlib + `repo_paths` + `shared.raw_minute` only): `_connect(path, write)` creates the parent folder and the schema on write; `write_minute` does `BEGIN IMMEDIATE`, inserts every `chains` row (`kind="chain"` per `minute.chains` with its `seq`, `kind="stored"` per `minute.stored`, `kind="term"` when `minute.term`), then `_insert_minute_row(conn, minute)`, then `COMMIT` (rollback on any exception, re-raise). `prune` deletes `<date>.db` plus its `-wal` / `-shm` for every session but the newest `keep_sessions`. In `backup_local.db_paths`, skip any path under `repo_paths.RAW_DIR` (resolved at call time) with a comment: *the raw feed is ~1 GB a session and is re-collected, never restored (design §4)*. Add `RAW_DIR` to `repo_paths.py` and `services/feed_svc/data/` to `.gitignore`.
- [ ] **Step 4: Run — expect PASS.** Also run `$PY -m pytest tools/tests -q -rf` (the backup list is derived from `.gitignore` by a test).
- [ ] **Step 5: Commit**
  ```bash
  git add shared/raw_store.py shared/tests/test_raw_store.py repo_paths.py .gitignore tools/backup_local.py tools/tests/test_backup_local.py
  git commit -m "feat(feed): the per-session raw chain store"
  ```

### Task 4: `config/feed.toml`, its loader and catalogue entries

**Files:**
- Create: `config/feed.toml`, `shared/feed_config.py`
- Modify: `webgui/config_schema.py`
- Test: `shared/tests/test_feed_config.py`

**Interfaces:**
- Produces: `feed_config.mode() -> str` (`"off" | "tee" | "on"`, anything else → `"off"`), `keep_sessions() -> int`, `stale_alarm_min() -> int`, `push_max_age_min() -> int`, `lag_alarm_min() -> int`, `shadow_enabled() -> bool` (`mode() == "tee"`). Each reads through `shared.config_toml.toml_loader("feed", defaults)` per call (mtime-cached by the loader), never raises, clamps non-positive integers to the default.

- [ ] **Step 1: Write the failing test**

```python
from shared import feed_config as fc


def test_defaults_ship_off(monkeypatch):
    monkeypatch.setattr(fc, "_load", lambda: {})
    assert (fc.mode(), fc.keep_sessions(), fc.stale_alarm_min(),
            fc.push_max_age_min(), fc.lag_alarm_min()) == ("off", 5, 2, 3, 5)


def test_an_unknown_mode_is_off_and_a_bad_number_is_the_default(monkeypatch):
    monkeypatch.setattr(fc, "_load", lambda: {"feed": {"mode": "ON!", "keep_sessions": -1}})
    assert fc.mode() == "off" and fc.keep_sessions() == 5
```

- [ ] **Step 2: Run — expect FAIL.** Run: `$PY -m pytest shared/tests/test_feed_config.py -q`
- [ ] **Step 3: Implement.** `config/feed.toml`:
  ```toml
  # The raw feed and catch-up (docs/plans/2026-10-14-raw-feed-and-catch-up-design.md).
  [feed]
  mode = "off"            # off = today; tee = the collector also writes raw + a shadow check; on = feed_svc fetches, options_svc derives
  keep_sessions = 5       # raw sessions kept on disk (~1 GB each); never backed up
  stale_alarm_min = 2     # feed_svc /health goes down after this long with no complete minute, inside the window

  [catchup]
  push_max_age_min = 3    # an alert from a caught-up minute older than this is recorded, not pushed
  lag_alarm_min = 5       # options_svc /health goes down when derivation is this far behind, inside the window
  ```
  `shared/feed_config.py` with `_load()` wrapping the loader so tests can patch it. Add a `Section("Raw feed and catch-up", ...)` in `webgui/config_schema.py` with a `Field` per key (mode as a choice of the three values).
- [ ] **Step 4: Run — expect PASS**, plus `$PY -m pytest webgui/tests/test_config_schema.py -q`.
- [ ] **Step 5: Commit**
  ```bash
  git add config/feed.toml shared/feed_config.py shared/tests/test_feed_config.py webgui/config_schema.py
  git commit -m "feat(feed): config/feed.toml and its loader"
  ```

### Task 5: `tee` — today's collector also writes the raw minute

**Files:**
- Modify: `services/options_svc/compute.py` (`collect_gex_snapshots`: one call after `fetch_minute`)
- Create: `services/options_svc/raw_tee.py`
- Test: `services/options_svc/tests/test_raw_tee.py`

**Interfaces:**
- Consumes: `raw_store.write_minute`, `raw_store.prune`, `feed_config.mode/keep_sessions`.
- Produces: `raw_tee.maybe_write(minute: RawMinute, root=None) -> bool` — writes only when `mode() == "tee"`; prunes once per session date per process; never raises (`_degrade.degraded("options.raw_tee")`), returns whether it wrote.

- [ ] **Step 1: Write the failing test**

```python
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import raw_tee
from shared import feed_config, raw_store
from shared.raw_minute import RawMinute

CT = ZoneInfo("America/Chicago")
TS = int(dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT).timestamp())


def _minute():
    return RawMinute(ts=TS, chains=[("SPY", {"symbol": "SPY"})], symbols=["SPY"])


def test_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "off")
    assert raw_tee.maybe_write(_minute(), root=tmp_path) is False
    assert raw_store.sessions(root=tmp_path) == []


def test_tee_writes_the_minute_under_its_ct_date(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    assert raw_tee.maybe_write(_minute(), root=tmp_path) is True
    assert raw_store.complete_minutes("2026-10-14", root=tmp_path) == [TS]


def test_a_store_failure_never_reaches_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "tee")
    monkeypatch.setattr(raw_store, "write_minute", lambda *a, **k: 1 / 0)
    assert raw_tee.maybe_write(_minute(), root=tmp_path) is False
```

- [ ] **Step 2: Run — expect FAIL.** Run: `$PY -m pytest services/options_svc/tests/test_raw_tee.py -q`
- [ ] **Step 3: Implement** `raw_tee.py`; the date is `datetime.fromtimestamp(minute.ts, CT).date().isoformat()`. In `collect_gex_snapshots`, right after `minute = gc.fetch_minute(...)`: `raw_tee.maybe_write(minute)` (imported at function level). `compute.py`'s line count must not rise: the ceiling test (`tests/test_compute_size.py` or its equivalent; find it with `grep -rn "compute.py" services/options_svc/tests | grep -i ceil`) will tell you; if needed, move one more helper from `compute` into `collect_derive`.
- [ ] **Step 4: Run — expect PASS**; then `$PY -m pytest services/options_svc -q -rf`.
- [ ] **Step 5: Commit**
  ```bash
  git add services/options_svc/raw_tee.py services/options_svc/tests/test_raw_tee.py services/options_svc/compute.py
  git commit -m "feat(feed): tee mode writes each collected minute to the raw store"
  ```

### Task 6: The shadow deriver — proof that derive-from-raw is identical, and its cost

**Files:**
- Create: `services/options_svc/raw_shadow.py`
- Modify: `services/options_svc/handlers.py` (`collect_gex_history`: submit the shadow after collection), `options-scanner/gex_history_db.py` (add `rows_at(conn, ts) -> list[tuple]` reading `snapshots` for one ts, grids decoded)
- Test: `services/options_svc/tests/test_raw_shadow.py`

**Interfaces:**
- Consumes: `raw_store.read_minute`, `gex_collector.derive_minute` (with `on_chain=None`: the shadow never runs the stateful hooks or a push), `gex_history_db.init_schema`, `rows_at`.
- Produces:
  ```python
  def compare_minute(date_iso: str, ts: int, live_conn, root=None) -> dict
      # {"ts", "rows", "diffs": [str], "ms": float}; derives into a fresh ":memory:" db
  def submit(date_iso: str, ts: int) -> bool     # one single-thread executor; False (and counted) when busy
  def stats() -> dict                            # {"minutes", "minutes_with_diffs", "skipped", "p50_ms", "p95_ms", "last_diff"}
  ```
  `handlers.collect_gex_history` publishes `stats()` to `cache:feed:shadow` (`bus.cache_set(..., skip_unchanged=False)`) when `feed_config.shadow_enabled()`.

- [ ] **Step 1: Write the failing test** — build a live in-memory DB by running `derive_minute` on a minute with a real-shaped chain (reuse the `_whole_chain` builder from `services/options_svc/tests/conftest.py`), write the same minute to the raw store, then:

```python
def test_identical_rows_compare_clean(tmp_path, live_conn, minute):
    raw_store.write_minute("2026-10-14", minute, root=tmp_path)
    out = raw_shadow.compare_minute("2026-10-14", minute.ts, live_conn, root=tmp_path)
    assert out["rows"] > 0 and out["diffs"] == []


def test_a_changed_cell_is_reported(tmp_path, live_conn, minute):
    raw_store.write_minute("2026-10-14", minute, root=tmp_path)
    live_conn.execute("UPDATE snapshots SET spot = spot + 1 WHERE view = 'gex'")
    out = raw_shadow.compare_minute("2026-10-14", minute.ts, live_conn, root=tmp_path)
    assert any("spot" in d for d in out["diffs"])


def test_a_busy_shadow_skips_rather_than_queueing(monkeypatch):
    monkeypatch.setattr(raw_shadow, "_busy", lambda: True)
    assert raw_shadow.submit("2026-10-14", 60) is False
    assert raw_shadow.stats()["skipped"] >= 1
```

  (`live_conn` and `minute` are fixtures in the test file: `minute` = `RawMinute(ts, chains=[("SPY", _whole_chain())], symbols=["SPY"])`; `live_conn` = `sqlite3.connect(":memory:")` + `gh.init_schema` + `gc.derive_minute(gamma_tool.GammaEngine(), conn, minute)`.)

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Compare `(symbol, view)` sets, every summary column with `math.isclose(rel_tol=1e-9)` (None equal only to None), and decoded grids cell by cell. The executor is `ThreadPoolExecutor(max_workers=1)`; `_busy()` is "a future is still running". The live side reads through `gh.connect(read_only=True)` inside the worker. Log a WARNING per minute with diffs (first 5), INFO once per 15 minutes with `stats()`. Guard with `_degrade.degraded("options.raw_shadow")`.
- [ ] **Step 4: Run — expect PASS**; `$PY -m pytest services/options_svc -q -rf`; `cd options-scanner && $PY -m pytest tests -q -rf`.
- [ ] **Step 5: Commit**
  ```bash
  git add services/options_svc/raw_shadow.py services/options_svc/tests/test_raw_shadow.py services/options_svc/handlers.py options-scanner/gex_history_db.py
  git commit -m "feat(feed): tee's shadow deriver compares derive-from-raw with the live rows"
  ```

**Operator gate after Phase 2 (not a code step):** merge, push, promote outside the window, set `mode = "tee"` in `config/local/feed.toml` (Settings → Configuration), restart options_svc after 15:20. After one full session read `cache:feed:shadow`: **`minutes_with_diffs == 0` and `p95_ms < 2000`** are the gate for Phase 3's `on`. Record the numbers in the CHANGELOG.

---

## Phase 3 — The cursor-driven deriver (options_svc)

### Task 7: The derive cursor, written in the same transaction as the rows

**Files:**
- Modify: `options-scanner/gex_history_db.py` (schema + two functions)
- Test: `options-scanner/tests/test_derive_cursor.py`

**Interfaces:**
- Produces:
  ```python
  # schema, in init_schema:
  # CREATE TABLE IF NOT EXISTS derive_cursor (session TEXT PRIMARY KEY, last_ts INTEGER NOT NULL)
  def get_cursor(conn, session: str) -> int | None
  def set_cursor(conn, session: str, ts: int) -> None     # no commit; the caller's transaction
  ```

- [ ] **Step 1: Write the failing test**

```python
import sqlite3

import pytest

import gex_history_db as gh


def _conn():
    c = sqlite3.connect(":memory:")
    gh.init_schema(c)
    return c


def test_cursor_round_trip():
    c = _conn()
    assert gh.get_cursor(c, "2026-10-14") is None
    gh.set_cursor(c, "2026-10-14", 600)
    c.commit()
    assert gh.get_cursor(c, "2026-10-14") == 600


def test_rows_and_cursor_commit_together_or_not_at_all():
    c = _conn()
    gh.insert_snapshot(c, "SPY", "gex", {"ts": 600, "spot": 1.0}, {"600": {"call": 1, "put": 1, "net": 0}}, 0)
    gh.set_cursor(c, "2026-10-14", 600)
    c.rollback()                       # the crash between "rows" and "commit"
    assert gh.get_cursor(c, "2026-10-14") is None
    assert c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
```

- [ ] **Step 2: Run — expect FAIL.** `cd options-scanner && $PY -m pytest tests/test_derive_cursor.py -q`
- [ ] **Step 3: Implement** (an `INSERT ... ON CONFLICT(session) DO UPDATE SET last_ts = excluded.last_ts`). Include the table in the retention purge: delete cursor rows whose session is older than the oldest kept session.
- [ ] **Step 4: Run — expect PASS** and the full options-scanner suite.
- [ ] **Step 5: Commit**
  ```bash
  git add options-scanner/gex_history_db.py options-scanner/tests/test_derive_cursor.py
  git commit -m "feat(gex): a derive cursor per session, committed with the minute's rows"
  ```

### Task 8: Flow alerts dated at the minute; pushes only when fresh

**Files:**
- Modify: `services/options_svc/handlers.py` (`run_flow_alerts(bus, as_of=None)`, `_flow_window_open(now=None)` already takes `now`)
- Test: `services/options_svc/tests/test_flow_alerts_as_of.py`

**Interfaces:**
- Produces: `run_flow_alerts(bus, as_of: datetime | None = None) -> None`. `as_of=None` is today's behaviour exactly. With `as_of`: the flow-window gate, `now_ts`, the alert `ts`, the cooldown stamps and the day key use `as_of`; a push is sent only when `_alert_now() - as_of <= timedelta(minutes=feed_config.push_max_age_min())`; a suppressed push increments `_degrade`-free counter `handlers.LATE_PUSHES_SUPPRESSED` and logs INFO once per pass with the count.

- [ ] **Step 1: Write the failing test** — patch `push_notify.send_flow_alert` to record, `compute.take_uoa_stash` to return one UOA hit, and pin `_alert_now` to 13:00 CT:

```python
def test_an_old_minute_is_recorded_but_not_pushed(bus, monkeypatch, one_uoa_hit):
    sent = []
    monkeypatch.setattr(push_notify, "send_flow_alert", lambda a, config=None: sent.append(a))
    monkeypatch.setattr(handlers, "_alert_now", lambda: dt.datetime(2026, 10, 14, 13, 0, tzinfo=CT))
    handlers.run_flow_alerts(bus, as_of=dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT))
    alerts = bus.cache_get(handlers.CACHE_FLOW_ALERTS).payload["alerts"]
    assert len(alerts) == 1 and alerts[0]["ts"] == int(dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT).timestamp())
    assert sent == []


def test_a_fresh_minute_still_pushes(bus, monkeypatch, one_uoa_hit):
    sent = []
    monkeypatch.setattr(push_notify, "send_flow_alert", lambda a, config=None: sent.append(a))
    monkeypatch.setattr(handlers, "_alert_now", lambda: dt.datetime(2026, 10, 14, 10, 44, tzinfo=CT))
    handlers.run_flow_alerts(bus, as_of=dt.datetime(2026, 10, 14, 10, 42, tzinfo=CT))
    assert len(sent) == 1
```

  (`one_uoa_hit` monkeypatches `compute.take_uoa_stash` to `{"SPY": [<a contract dict shaped like detect_uoa's output — copy one from test_flow_alerts*.py>]}` and the other stashes/loaders to empty.)

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** — thread `as_of` through; keep the default path untouched. Also give `publish_flow_skew`, `publish_matrix` and `publish_net_premium` no new argument: they read the DB, which holds every row up to the minute just derived.
- [ ] **Step 4: Run — expect PASS**; `$PY -m pytest services/options_svc -q -rf`.
- [ ] **Step 5: Commit**
  ```bash
  git add services/options_svc/handlers.py services/options_svc/tests/test_flow_alerts_as_of.py
  git commit -m "feat(flow): alerts dated at their minute; a caught-up minute is recorded, not pushed"
  ```

### Task 9: The deriver loop, state warm-up and catch-up

**Files:**
- Create: `services/options_svc/deriver.py`
- Modify: `services/options_svc/scheduler.py` (the `gex_due` branch: `mode == "on"` → the deriver branch, run EVERY tick, not gated on the window). `deriver.lag_min()` is wired into `/health` by Task 10.
- Test: `services/options_svc/tests/test_deriver.py`

**Interfaces:**
- Consumes: `raw_store.sessions/complete_minutes/read_minute/gaps`, `collect_derive.build_context/derive_into/finish`, `gex_history_db.get_cursor/set_cursor`, `handlers.run_flow_alerts(bus, as_of=...)`, the publishes and `refresh_gamma_current`.
- Produces:
  ```python
  def pending(conn, root=None, keep=None) -> list[tuple[str, int]]   # (session, ts) oldest first, every kept session
  def warm_up(conn, session: str, root=None) -> None   # replays session minutes <= cursor through the STATEFUL hooks only
  def run_pass(bus, root=None, max_minutes=None) -> dict   # {"derived": n, "lag_min": float, "behind": bool}
  def lag_min(now=None) -> float | None                  # minutes between the newest raw minute and the cursor; None when mode != "on"
  def reset_process_state() -> None                      # forget which sessions were warmed up (a new process)
  ```
  Per minute in `run_pass`: `ctx = build_context(ts, capture)`; `derive_into(conn, minute, ctx)`; `set_cursor(conn, session, ts)`; `conn.commit()`; `finish(ctx)`; `handlers.run_flow_alerts(bus, as_of=ctx.minute_dt)`. A `gaps` minute: `set_cursor` only (no rows), commit. After the LAST minute of the pass: `publish_flow_skew`, `publish_matrix`, `publish_net_premium`, then `refresh_gamma_current` (the capture stash holds the last minute's chains). `warm_up` runs once per (process, session) before the first `run_pass` minute of that session: `collect_derive.build_context(ts, capture=set())` + its `on_chain` per chain for every complete minute `<= cursor`, with `engine=None` and NO writes and NO alerts.

- [ ] **Step 1: Write the failing tests** — the replay-determinism test is the key one. Build 6 raw minutes of a real-shaped chain whose `totalVolume` grows minute to minute (so the bought/sold tallies, traded premium and HIRO all move), in `tmp_path`:

```python
import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo

import pytest

import gex_history_db as gh
from services.options_svc import deriver, flow_sides_tick, handlers, traded_premium
from services.options_svc.tests.conftest import _whole_chain
from shared import feed_config, raw_store
from shared.raw_minute import RawMinute

CT = ZoneInfo("America/Chicago")
T0 = int(dt.datetime(2026, 10, 14, 10, 0, tzinfo=CT).timestamp())


def _chain_at(i):
    ch = _whole_chain()
    for side in ("callExpDateMap", "putExpDateMap"):
        for strikes in ch[side].values():
            for legs in strikes.values():
                for leg in legs:
                    leg["totalVolume"] = 100 * (i + 1)      # volume grows each minute
    return ch


@pytest.fixture
def six_minutes(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "on")
    for i in range(6):
        raw_store.write_minute("2026-10-14", RawMinute(
            ts=T0 + 60 * i, chains=[("SPY", _chain_at(i))], symbols=["SPY"]), root=tmp_path)
    return tmp_path


@pytest.fixture
def fresh_db(monkeypatch):
    def make():
        conn = sqlite3.connect(":memory:")
        gh.init_schema(conn)
        monkeypatch.setattr(gh, "connect", lambda read_only=False: conn)
        return conn
    return make


def _dump(conn, bus):
    rows = conn.execute("SELECT symbol, view, ts, spot, net_total, call_vol, put_vol, "
                        "call_prem, put_prem, gex_json FROM snapshots ORDER BY 1, 2, 3").fetchall()
    hiro = conn.execute("SELECT * FROM hiro_minutes ORDER BY 1, 2").fetchall()
    cur = conn.execute("SELECT * FROM derive_cursor").fetchall()
    env = bus.cache_get(handlers.CACHE_FLOW_ALERTS)
    return rows, hiro, cur, (env.payload if env else None)


def _run_all(bus, raw_root, conn, kill_after=None):
    deriver.reset_process_state()          # forget warm-ups, as a new process would
    if kill_after is None:
        deriver.run_pass(bus, root=raw_root)
    else:
        deriver.run_pass(bus, root=raw_root, max_minutes=kill_after)
        deriver.reset_process_state()      # the crash
        flow_sides_tick.reset_for_tests(); traded_premium.reset_for_tests()   # in-memory state lost
        deriver.run_pass(bus, root=raw_root)
    return _dump(conn, bus)


def test_a_crash_and_catch_up_equals_never_stopping(six_minutes, fresh_db):
    from shared.bus import Bus
    from shared.bus.client import reset_fake_bus
    reset_fake_bus()
    straight = _run_all(Bus(), six_minutes, fresh_db())
    reset_fake_bus()
    crashed = _run_all(Bus(), six_minutes, fresh_db(), kill_after=2)
    assert crashed == straight
    assert straight[2] == [("2026-10-14", T0 + 300)]


def test_minutes_are_derived_oldest_first_across_sessions(tmp_path, fresh_db, monkeypatch):
    monkeypatch.setattr(feed_config, "mode", lambda: "on")
    conn = fresh_db()
    y0 = int(dt.datetime(2026, 10, 13, 15, 19, tzinfo=CT).timestamp())
    raw_store.write_minute("2026-10-13", RawMinute(ts=y0, chains=[("SPY", _chain_at(0))]), root=tmp_path)
    for i in range(3):
        raw_store.write_minute("2026-10-14", RawMinute(ts=T0 + 60 * i, chains=[("SPY", _chain_at(i))]),
                               root=tmp_path)
    assert deriver.pending(conn, root=tmp_path) == [
        ("2026-10-13", y0), ("2026-10-14", T0), ("2026-10-14", T0 + 60), ("2026-10-14", T0 + 120)]


def test_a_gap_minute_moves_the_cursor_and_writes_no_rows(tmp_path, fresh_db, monkeypatch):
    from shared.bus import Bus
    monkeypatch.setattr(feed_config, "mode", lambda: "on")
    conn = fresh_db()
    raw_store.write_minute("2026-10-14", RawMinute(ts=T0, chains=[("SPY", _chain_at(0))]), root=tmp_path)
    raw_store.write_gap("2026-10-14", T0 + 60, "proxy unreachable", root=tmp_path)
    raw_store.write_minute("2026-10-14", RawMinute(ts=T0 + 120, chains=[("SPY", _chain_at(2))]),
                           root=tmp_path)
    deriver.run_pass(Bus(), root=tmp_path)
    ts_written = {r[0] for r in conn.execute("SELECT DISTINCT ts FROM snapshots")}
    assert ts_written == {T0, T0 + 120}
    assert gh.get_cursor(conn, "2026-10-14") == T0 + 120


def test_views_publish_once_per_pass_not_per_minute(six_minutes, fresh_db, monkeypatch):
    from shared.bus import Bus
    fresh_db()
    calls = []
    monkeypatch.setattr(handlers, "publish_matrix", lambda bus: calls.append(1))
    deriver.reset_process_state()
    deriver.run_pass(Bus(), root=six_minutes)          # 6 pending minutes
    assert calls == [1]
```

  `pending` must list a `gaps` minute too (it moves the cursor): `complete_minutes` ∪ gap minutes after the cursor, sorted.

  If `flow_sides_tick` / `traded_premium` lack a `reset_for_tests`, add one (they have module state; the options_svc conftest's `_fresh_flow_sides_state` shows how it is reset today — reuse that).

- [ ] **Step 2: Run — expect FAIL.** `$PY -m pytest services/options_svc/tests/test_deriver.py -q`
- [ ] **Step 3: Implement** `deriver.py` (imports nothing from `compute`). The connection is `gex_history_db.connect()` per pass. The capture set is what `handlers.collect_gex_history` computes today: move that computation into `handlers._capture_symbols(bus)` and call it from both. In `scheduler.py`, when `feed_config.mode() == "on"`: replace the `gex_due` branch with a `deriver` branch launched every tick (keyed, so it never doubles), whose body is `run_pass(bus)`; the `tee`/`off` path is unchanged.
- [ ] **Step 4: Run — expect PASS**; full `$PY -m pytest services/options_svc -q -rf`.
- [ ] **Step 5: Commit**
  ```bash
  git add services/options_svc/deriver.py services/options_svc/tests/test_deriver.py services/options_svc/scheduler.py services/options_svc/handlers.py services/options_svc/flow_sides_tick.py services/options_svc/traded_premium.py
  git commit -m "feat(options): derive from the raw store behind a cursor; catch up after any stop"
  ```

### Task 10: `/health` carries a service's own facts (`make_app(health_extra=...)`)

**Files:**
- Modify: `services/_scaffold.py` (`make_app` new kwarg; the `/health` route merges it), `services/options_svc/app.py`
- Test: `services/tests/test_scaffold_health_extra.py`

**Interfaces:**
- Produces: `make_app(..., health_extra: Callable[[], tuple[dict, str | None]] | None = None)`. The route merges the dict into its answer; a non-None reason makes `up: false` with that reason (unless already down, whose reason wins). A raising `health_extra` → `up` unchanged, `health_extra_error: "<type>"`, `_degrade.degraded("scaffold.health_extra")`.
- options_svc passes `lambda: ({"derive_lag_min": deriver.lag_min()}, "derivation is N min behind" if lag > lag_alarm_min inside the window else None)`.

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from services._scaffold import make_app


def test_extra_facts_and_a_reason_reach_health(fake_bus):
    app = make_app("t", bus=fake_bus, health_extra=lambda: ({"lag": 7}, "7 min behind"))
    with TestClient(app) as c:
        h = c.get("/health").json()
    assert h["lag"] == 7 and h["up"] is False and h["reason"] == "7 min behind"


def test_a_raising_extra_never_breaks_health(fake_bus):
    app = make_app("t", bus=fake_bus, health_extra=lambda: 1 / 0)
    with TestClient(app) as c:
        h = c.get("/health").json()
    assert h["up"] is True and h["health_extra_error"] == "ZeroDivisionError"
```

  (`fake_bus`: copy the fixture the existing `services/tests/test_scaffold*.py` use.)
- [ ] **Step 2: Run — expect FAIL.** `$PY -m pytest services/tests/test_scaffold_health_extra.py -q`
- [ ] **Step 3: Implement**; wire options_svc.
- [ ] **Step 4: Run — expect PASS**; `$PY -m pytest services/tests -q -rf` and the options_svc suite.
- [ ] **Step 5: Commit**
  ```bash
  git add services/_scaffold.py services/options_svc/app.py services/tests/test_scaffold_health_extra.py
  git commit -m "feat(services): /health can carry a service's own facts; options_svc reports derive lag"
  ```

---

## Phase 4 — `feed_svc`

### Task 11: One fetch plan for both fetchers

**Files:**
- Create: `services/options_svc/collect_plan.py`
- Modify: `services/options_svc/compute.py` (`_gth_symbols` and the tier assembly in `collect_gex_snapshots` call the plan), `services/options_svc/handlers.py` (publish the capture set to `cache:options:collect_capture` each collect / each deriver pass: `{"symbols": [...], "ts": <unix>}`)
- Test: `services/options_svc/tests/test_collect_plan.py`

**Interfaces:**
- Produces (imports nothing from `compute`):
  ```python
  def plan_symbols(now, eth_payload) -> list[str] | None     # None = full universe, [] = skip (today's _gth_symbols contract)
  def plan_tiers(universe, flow_cfg, capture: set | None) -> dict | None
      # today's collection_tiers(universe, base=gc.SYMBOLS, capture=..., hiro=<[hiro].symbols>, flip=flip_alert_symbols(flow_cfg))
      # capture None (unknown) -> every symbol core: no tail
  def capture_from_cache(payload, now_ts, max_age_s=180) -> set | None   # stale/missing -> None
  ```

- [ ] **Step 1: Write the failing tests** — first re-point the existing `_gth_symbols` tests (`grep -rn "_gth_symbols" services/options_svc/tests`) at `collect_plan.plan_symbols`, passing the ETH payload they used to patch in; then add `services/options_svc/tests/test_collect_plan.py`:

```python
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import collect_plan as cp

CT = ZoneInfo("America/Chicago")


def test_unknown_capture_means_no_tail():
    t = cp.plan_tiers(["SPY", "AAPL", "SOFI"], flow_cfg={}, capture=None)
    assert t is None or not t.get("tail")


def test_a_stale_capture_payload_is_unknown():
    now = 1_760_000_000
    assert cp.capture_from_cache({"symbols": ["SPY"], "ts": now - 181}, now) is None
    assert cp.capture_from_cache({"symbols": ["SPY"], "ts": now - 30}, now) == {"SPY"}
    assert cp.capture_from_cache(None, now) is None


def test_regular_hours_poll_the_full_universe():
    assert cp.plan_symbols(dt.datetime(2026, 10, 14, 10, 0, tzinfo=CT), eth_payload=None) is None
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement**; `compute._gth_symbols` becomes a two-line wrapper that reads the ETH cache and calls `plan_symbols` (its tests keep passing).
- [ ] **Step 4: Run — expect PASS**; full options_svc suite.
- [ ] **Step 5: Commit**
  ```bash
  git add services/options_svc/collect_plan.py services/options_svc/tests/test_collect_plan.py services/options_svc/compute.py services/options_svc/handlers.py
  git commit -m "refactor(options): one fetch plan (universe and tiers) for every fetcher"
  ```

### Task 12: `feed_svc` — fetch, stamp, write

**Files:**
- Create: `services/feed_svc/__init__.py`, `services/feed_svc/app.py`, `services/feed_svc/scheduler.py`, `services/feed_svc/ingest.py`, `services/feed_svc/tests/__init__.py`, `services/feed_svc/tests/test_ingest.py`
- Modify: `config/ports.toml` (`[services] feed = 8218   # the raw chain feed (feed_svc)`), `.github/workflows/ci.yml` (a `feed_svc` row), `webgui/pages/status.py` only if `restart_spec` needs the new name (its test will say)
- Test: `services/feed_svc/tests/test_ingest.py`, `tests/test_ci_covers_every_suite.py` (existing)

**Interfaces:**
- Consumes: `gex_collector.fetch_minute`, `collect_plan.*`, `raw_store.*`, `feed_config.*`, `proxy_client`'s `schwab_py_client` (the same accessor `compute` uses: `services/options_svc/compute.py` imports it as `_proxy`; import that module, not `compute`).
- Produces:
  ```python
  # ingest.py
  def tick(now, client, bus, root=None) -> str     # "wrote" | "skipped:<why>" | "gap:<why>"
  def note_restart_gaps(now, root=None) -> int     # minutes between the last complete minute and now, in the window, -> gaps("feed down")
  def health(now=None, root=None) -> tuple[dict, str | None]   # {"last_complete_minute", "mode"}, reason when stale
  ```
  `tick` runs only when `feed_config.mode() == "on"`, on a trading day inside `mc.in_collection_window(now, eth_eligible=True)`, once per minute slot. On a fetch where EVERY symbol failed: `write_gap(..., "proxy unreachable")`. It prunes at the first write of a session. It writes nothing to Redis but its heartbeat and `cache:feed:status` (`{"last_complete_minute", "gaps_today", "mode"}`).

- [ ] **Step 1: Write the failing tests**

```python
def test_on_writes_one_complete_minute(tmp_path, monkeypatch, client_double):
    monkeypatch.setattr(feed_config, "mode", lambda: "on")
    assert ingest.tick(RTH, client_double, bus=None, root=tmp_path) == "wrote"
    assert raw_store.complete_minutes("2026-10-14", root=tmp_path) == [int(RTH.timestamp())]


def test_off_and_tee_never_fetch(tmp_path, monkeypatch, client_double):
    for mode in ("off", "tee"):
        monkeypatch.setattr(feed_config, "mode", lambda m=mode: m)
        assert ingest.tick(RTH, client_double, bus=None, root=tmp_path).startswith("skipped")
    assert client_double.get_option_chain.call_count == 0


def test_a_restart_records_the_missed_minutes_as_gaps(tmp_path, monkeypatch, client_double):
    monkeypatch.setattr(feed_config, "mode", lambda: "on")
    ingest.tick(RTH, client_double, bus=None, root=tmp_path)
    assert ingest.note_restart_gaps(RTH + dt.timedelta(minutes=4), root=tmp_path) == 3
    assert [r for _, r in raw_store.gaps("2026-10-14", root=tmp_path)] == ["feed down"] * 3


def test_health_goes_down_when_no_minute_completes(tmp_path, monkeypatch, client_double):
    monkeypatch.setattr(feed_config, "mode", lambda: "on")
    ingest.tick(RTH, client_double, bus=None, root=tmp_path)
    facts, reason = ingest.health(RTH + dt.timedelta(minutes=3), root=tmp_path)
    assert reason and "no complete minute" in reason


def test_feed_svc_imports_no_engine():
    import subprocess, sys
    out = subprocess.run([sys.executable, "-c",
        "import services.feed_svc.app, sys; print(sorted(m for m in sys.modules if m in "
        "('gamma_tool','scanner_engine','services.options_svc.compute')))"],
        capture_output=True, text=True, cwd=REPO_ROOT, env={**os.environ, "TZ": "America/Chicago"})
    assert out.stdout.strip() == "[]"
```

  (`client_double`: the `_client()` from `options-scanner/tests/test_gex_collector_tiers.py`, copied into this test module — the two suites run separately.)
- [ ] **Step 2: Run — expect FAIL.** `$PY -m pytest services/feed_svc -q`
- [ ] **Step 3: Implement.** `app.py` mirrors `services/news_svc/app.py` with `make_app("feed", scheduler=scheduler.loop, health_extra=ingest.health)`. `scheduler.loop` wakes every 5 s, beats `_heartbeat`, calls `note_restart_gaps` once at start, then `tick` when a new minute slot is due (the fetch runs in `run_in_executor`; a tick still running skips the next slot and records `gap:"previous minute still fetching"`). `gex_collector` lives in `options-scanner/`: add that folder to `sys.path` exactly as `services/options_svc/app.py` does (copy its block). Add `feed = 8218` to `config/ports.toml` and the CI row. Run `$PY -m pytest tests/test_systemd_units.py -q` — a new service appears in the generated units automatically; Task 13 moves it to the feed target.
- [ ] **Step 4: Run — expect PASS**: `$PY -m pytest services/feed_svc -q -rf`, `$PY -m pytest tests -q -rf`.
- [ ] **Step 5: Commit**
  ```bash
  git add services/feed_svc config/ports.toml .github/workflows/ci.yml
  git commit -m "feat(feed): feed_svc fetches each minute's chains into the raw store"
  ```

---

## Phase 5 — Two targets and a promote that leaves the feed running

### Task 13: The feed target

**Files:**
- Modify: `deploy/systemd/generate_units.py` (`feed_target_name()`, `FEED_COMPONENTS = ("proxy", "feed_svc")`, `_service_text` `PartOf`/`WantedBy` by membership, `_target_text` for both, `render_all`)
- Test: `tests/test_systemd_units.py` (new cases)

**Interfaces:**
- Produces: `feed_target_name() -> "trading-<env>-feed.target"`; `members(target) -> list[str]` (unit names). The app target text gains `Wants=<feed target>` and `After=<feed target>`. `feed_svc` gets `After=` + `Requires=` the proxy and the proxy `wait_http` `ExecStartPre` (it calls the proxy). In dev (`OWNS_PROXY` false) the feed target holds only `feed_svc`.

- [ ] **Step 1: Write the failing tests**

```python
def test_proxy_and_feed_belong_to_the_feed_target():
    units = gu.render_all()
    for comp in ("proxy", "feed_svc"):
        text = units[gu.unit_name(comp)]
        assert f"PartOf={gu.feed_target_name()}" in text
        assert f"WantedBy={gu.feed_target_name()}" in text
        assert f"PartOf={gu.target_name()}" not in text


def test_the_app_target_pulls_the_feed_in_but_does_not_own_it():
    app = gu.render_all()[gu.target_name()]
    wants = next(line for line in app.splitlines() if line.startswith("Wants="))
    assert gu.feed_target_name() in wants.split()[0] or gu.feed_target_name() in wants
    assert gu.unit_name("proxy") not in wants and gu.unit_name("feed_svc") not in wants
    assert f"After={gu.feed_target_name()}" in app


def test_every_other_service_stays_in_the_app_target():
    units = gu.render_all()
    for comp in ("options_svc", "webgui", "webgui_live"):
        assert f"PartOf={gu.target_name()}" in units[gu.unit_name(comp)]
```

- [ ] **Step 2: Run — expect FAIL.** `$PY -m pytest tests/test_systemd_units.py -q`
- [ ] **Step 3: Implement**; `install()` writes both targets; `activate()` enables both (`enable --now` the feed target only through the app target's `Wants=`; document in the module docstring that `systemctl --user enable --now trading-<env>.target` is still the one command).
- [ ] **Step 4: Run — expect PASS**; `$PY -m pytest tests deploy -q -rf`.
- [ ] **Step 5: Commit**
  ```bash
  git add deploy/systemd/generate_units.py tests/test_systemd_units.py
  git commit -m "feat(deploy): the proxy and feed_svc form their own systemd target"
  ```

### Task 14: The feed's file set and the market-hours promote

**Files:**
- Create: `tools/feed_files.py`, `tools/tests/test_feed_files.py`, `tools/tests/test_promote_plan.py`
- Modify: `tools/promote_facts.py` (a `plan` subcommand), `tools/promote.sh`
- Test: as listed

**Interfaces:**
- Produces:
  ```python
  # tools/feed_files.py
  def closure(entrypoints=("schwab-proxy/schwab_proxy.py", "services/feed_svc/app.py"), root=ROOT) -> set[str]
      # repo-relative paths: every repo .py reached by ANY import statement (module level or inside a function),
      # resolved against the sys.path each entrypoint sets up (repo root, its own folder, options-scanner/),
      # plus every config/*.toml a reached module names in a toml_loader("<name>") call, plus requirements.lock
  # tools/promote_facts.py
  def plan(changed: list[str], now: datetime) -> dict
      # {"IN_WINDOW": "1"/"0", "FEED_TOUCHED": "<space-separated>", "APP_ONLY": "1"/"0", "APP_PORTS": "8210 8211 ... 8500 8501"}
      # IN_WINDOW: trading day and 06:30 <= now < 15:20 CT (mc.in_collection_window(now, eth_eligible=True))
      # APP_ONLY = IN_WINDOW and not FEED_TOUCHED
  ```
  `promote.sh`: after computing `NEW`, run `"$PY" tools/promote_facts.py plan "$PREV" "$NEW"` and `eval` it. If `IN_WINDOW=1` and `FEED_TOUCHED` is non-empty and `--feed` was not given: print the files, `"[promote] this changes the feed during the collection window (06:30-15:20 CT); promote after 15:20, or pass --feed to accept a gap"`, exit 1 BEFORE the stop. When `APP_ONLY=1`, `TARGET` stays the app target (`trading-prod.target`) and `stop_stack`'s port wait greps only `$APP_PORTS`; otherwise stop the app target, then the feed target, and start the feed target before the app target. After `probe_stack`, when `APP_ONLY=1`, print the deriver's lag from `http://127.0.0.1:<options port>/health` (`derive_lag_min`). The rollback trap stops and restarts exactly what was stopped.

- [ ] **Step 1: Write the failing tests**

```python
# tools/tests/test_feed_files.py
from tools import feed_files


def test_closure_holds_the_proxy_the_fetch_half_and_the_store():
    got = feed_files.closure()
    for must in ("schwab-proxy/schwab_proxy.py", "services/feed_svc/ingest.py",
                 "options-scanner/gex_collector.py", "shared/raw_store.py",
                 "shared/raw_minute.py", "repo_paths.py", "config/feed.toml",
                 "requirements.lock"):
        assert must in got, must


def test_a_function_level_import_is_followed(tmp_path):
    (tmp_path / "entry.py").write_text("def f():\n    import lazy_mod\n")
    (tmp_path / "lazy_mod.py").write_text("X = 1\n")
    assert "lazy_mod.py" in feed_files.closure(entrypoints=("entry.py",), root=tmp_path)


def test_the_trade_idea_module_is_not_a_feed_file():
    assert "services/options_svc/trade_idea.py" not in feed_files.closure()
```

```python
# tools/tests/test_promote_plan.py
import datetime as dt
from zoneinfo import ZoneInfo

from tools import promote_facts as pf

CT = ZoneInfo("America/Chicago")
TUE_1000 = dt.datetime(2026, 10, 13, 10, 0, tzinfo=CT)
TUE_1530 = dt.datetime(2026, 10, 13, 15, 30, tzinfo=CT)


def test_an_app_change_in_the_window_restarts_the_app_only():
    p = pf.plan(["services/options_svc/trade_idea.py"], TUE_1000)
    assert (p["IN_WINDOW"], p["FEED_TOUCHED"], p["APP_ONLY"]) == ("1", "", "1")
    assert "8100" not in p["APP_PORTS"].split() and "8218" not in p["APP_PORTS"].split()


def test_a_feed_change_in_the_window_is_named():
    p = pf.plan(["options-scanner/gex_collector.py", "README.md"], TUE_1000)
    assert p["FEED_TOUCHED"] == "options-scanner/gex_collector.py" and p["APP_ONLY"] == "0"


def test_after_the_window_everything_restarts():
    p = pf.plan(["services/options_svc/trade_idea.py"], TUE_1530)
    assert (p["IN_WINDOW"], p["APP_ONLY"]) == ("0", "0")
```

- [ ] **Step 2: Run — expect FAIL.** `$PY -m pytest tools/tests/test_feed_files.py tools/tests/test_promote_plan.py -q`
- [ ] **Step 3: Implement** `feed_files.closure` with `ast` (walk every `Import` / `ImportFrom` node in the whole tree, not only module level; resolve `from . import x` relative to the file; ignore modules that resolve outside the repo). `promote_facts.py plan PREV NEW` runs `git diff --name-only PREV NEW` itself and prints the plan's lines through `shell_lines`. Then edit `promote.sh` as described — keep every existing pre-stop check in its place and ahead of the stop.
- [ ] **Step 4: Run — expect PASS**; `$PY -m pytest tools/tests -q -rf`. Also `bash -n tools/promote.sh` (syntax) and `shellcheck tools/promote.sh` if installed.
- [ ] **Step 5: Commit**
  ```bash
  git add tools/feed_files.py tools/promote_facts.py tools/promote.sh tools/tests/test_feed_files.py tools/tests/test_promote_plan.py
  git commit -m "feat(promote): in the collection window restart the app only, and refuse a feed change"
  ```

---

## Phase 6 — Visibility and documentation

### Task 15: The Status page's Feed card

**Files:**
- Modify: `webgui/pages/status.py` (a Feed card: last raw minute, deriver lag, today's raw gaps with reasons, mode), `webgui/page_help.py` (Status help: what the card means and what a gap is)
- Test: `webgui/tests/test_status_feed_card.py`

**Interfaces:**
- Consumes: `cache:feed:status` (Task 12), options_svc `/health` `derive_lag_min` (Task 10), `cache:feed:shadow` (Task 6) while `mode = "tee"`.

- [ ] **Step 1: Write the failing test** — the card's rows come from a pure function `feed_card_rows(status, lag, shadow, mode) -> list[tuple[str, str]]` in `webgui/pages/status.py`:

```python
from pages.status import feed_card_rows


def test_off_says_where_collection_runs():
    assert feed_card_rows(None, None, None, "off") == [
        ("Mode", "Off - collection runs inside the options service")]


def test_tee_shows_the_shadow_check():
    rows = dict(feed_card_rows(None, None, {"minutes": 412, "minutes_with_diffs": 0,
                                            "p95_ms": 840.0}, "tee"))
    assert rows["Shadow check"] == "412 minutes, 0 with differences"
    assert rows["Derive time (p95)"] == "840 ms"


def test_on_lists_every_gap_with_its_reason():
    import datetime as dt
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    g1 = int(dt.datetime(2026, 10, 14, 8, 53, tzinfo=ct).timestamp())
    g2 = int(dt.datetime(2026, 10, 14, 9, 10, tzinfo=ct).timestamp())
    status = {"last_complete_minute": g2 + 3600,
              "gaps_today": [[g1, "feed down"], [g2, "proxy unreachable"]]}
    rows = feed_card_rows(status, 0.0, None, "on")
    assert ("Behind by", "0 min") in rows
    assert [r for r in rows if r[0] == "Gap"] == [
        ("Gap", "08:53 CT - feed down"), ("Gap", "09:10 CT - proxy unreachable")]


def test_a_lag_past_the_alarm_says_behind():
    rows = dict(feed_card_rows({"last_complete_minute": 1, "gaps_today": []}, 7.0, None, "on"))
    assert rows["Behind by"] == "7 min - behind"
```

  ("behind" appears when `lag > feed_config.lag_alarm_min()`.)
- [ ] **Step 2: Run — expect FAIL.** `cd webgui && $PY -m pytest tests/test_status_feed_card.py -q` (webgui tests run from inside `webgui/`)
- [ ] **Step 3: Implement** with the page kit (`kit.*`, Tailwind classes only — `test_no_inline_style.py`, `test_ui_kit_guard.py`); numbers through `pages/fmt.py`.
- [ ] **Step 4: Run — expect PASS**; `cd webgui && $PY -m pytest tests -q -rf`.
- [ ] **Step 5: Commit**
  ```bash
  git add webgui/pages/status.py webgui/page_help.py webgui/tests/test_status_feed_card.py
  git commit -m "feat(status): a Feed card - last raw minute, derive lag, today's gaps"
  ```

### Task 16: Documentation

**Files:**
- Modify: `CLAUDE.md` (unit order and ports: the eighth service and 8218, the two targets; the promote rule "in the window, the app only"; folder map row for `services/feed_svc`; the Tier-2 list), `docs/reference/running-and-environments.md`, `docs/dev-prod-environments.md` (runbook: `mode` cut-over steps and `--feed`), `docs/manuals/technical-reference/technical-reference.md` (collection → raw → derive, catch-up, gaps), `docs/manuals/api-reference/api-reference.md` (`cache:feed:status`, `cache:feed:shadow`, `cache:options:collect_capture`, the `/health` fields, the raw store schema), `docs/CHANGELOG.md`; rebuild the two manuals (`cd docs/manuals && $PY build_docs.py technical-reference api-reference`)
- Test: `$PY -m pytest tests/test_claude_md_size.py -q` (CLAUDE.md must stay under its ceiling — keep the additions to the rule lines; detail goes in `docs/reference/running-and-environments.md`)

- [ ] **Step 1:** Edit each file (correct in place; no "superseded" notes). CLAUDE.md: replace "Unit order: Redis → proxy → services → webgui" with the two-target statement; ports list gains 8218; replace the promote paragraph's "stops the whole target" with the window rule.
- [ ] **Step 2:** Rebuild the manuals; run `$PY -m pytest tests -q -rf`.
- [ ] **Step 3: Commit**
  ```bash
  git add CLAUDE.md docs/reference/running-and-environments.md docs/dev-prod-environments.md docs/manuals/technical-reference docs/manuals/api-reference docs/CHANGELOG.md
  git commit -m "docs: the raw feed, catch-up and the market-hours promote"
  ```

---

## Cut-over runbook (operator, after the code is promoted)

1. **Phase 1–2 promoted, outside the window.** `mode = "off"` → nothing changes.
2. **`tee` for one full session.** Settings → Configuration → Raw feed → mode `tee`; restart options_svc after 15:20. Next session, the Status card shows shadow minutes, differences and p95. Gate: **0 differences, p95 < 2 s.**
3. **Phases 3–5 promoted, outside the window.** Units regenerate (the feed target appears).
4. **`on`.** Set mode `on` after 15:20; restart the app target and the feed target. Next morning, confirm the Feed card's last raw minute ticks from 06:30/08:00, lag stays ≤ 1, and the Gamma heatmap fills.
5. **Prove the point.** During the session, `systemctl --user restart trading-prod-options_svc` and watch the lag climb then fall to ≤ 1 with no hole in the heatmap; then a market-hours promote of an app-only commit (the window rule restarts the app only).
6. **Back out** at any step: mode `off` + restart options_svc.
