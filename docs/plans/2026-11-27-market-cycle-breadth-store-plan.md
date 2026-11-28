# Market cycle, slice 1: the breadth store and its backfills — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily market-breadth history that is kept forever: twenty years backfilled from Schwab, a row added by `market_svc` after every close and corrected the next morning, and a loader for series pulled from TradingView.

**Architecture:** One SQLite store with a single write path (`shared/breadth_history.py`). `market_svc` gets one new sibling module (`breadth_daily.py`: pure row builders plus a `refresh` beside the poll). One CLI (`tools/breadth_backfill.py`) does the Schwab backfill, the CSV load and a status report. Nothing is published to Redis and no page changes: this slice only starts the history.

**Tech stack:** Python 3.11, sqlite3, `requests` through the service's existing proxy session, pytest. No new dependency.

**Spec:** [2026-11-27-market-cycle-design.md](2026-11-27-market-cycle-design.md), sections "Data", "The breadth store" and "Build order" item 1. Read it first. The engine, the view and every surface are later slices and are out of scope here.

## Global constraints

- Work in a worktree. Never `git pull`, `merge`, `checkout` or `reset` in `/home/administrator/dev`: that checkout is prod. Code reaches prod only through `tools/promote.sh`.
- No hard-coded path or port: paths come from `repo_paths`, the proxy from `repo_paths.PROXY_URL`.
- Every operator-tunable value lives in `config/breadth.toml`, read through `shared/config_toml.toml_loader`, with an entry in `webgui/config_schema.py`. The loader returns defaults on a missing file, key or bad value and never raises.
- A store's `db_path=None` is resolved at call time, never bound as a default at `def` time. Tests use `tmp_path`; the suite cannot open `shared/data`.
- A missing or unusable value is ABSENT. Never store 0 for "no reading". Numeric guards come from `shared.numeric.finite`; add no private `_num` / `_finite`.
- A timezone-naive datetime means Central time. Convert a Schwab candle stamp with an aware conversion to `America/Chicago`.
- A swallowed exception in a guard of 15 lines or more calls `_degrade.degraded("market.breadth")`.
- `breadth_daily.py` imports nothing from `market_read`, `new_highs` or `site_weather`. It may import `compute` for the proxy session only.
- No TradingView-sourced value is written to a cache view or a site file. In this slice nothing is written to Redis at all.
- No pytest config in a sub-folder. Write test files with the editor, never a heredoc.
- Commit messages end with the session's `Co-Authored-By` line. `F401` / `F811` findings are fixed by hand, never by `ruff --fix`.

**Running tests.** The venv is in the main checkout. From the worktree root:

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
"$PY" -m pytest shared/tests/test_breadth_history.py -v      # shared: from the repo root
"$PY" -m pytest services/market_svc -v                       # one service suite, on its own
"$PY" -m pytest tools/tests/test_breadth_backfill.py -v
cd webgui && "$PY" -m pytest tests/test_config_schema.py -v  # webgui: from inside its folder
```

## Facts measured on prod (2026-11-27) that the code depends on

- `GET {PROXY_URL}/pricehistory?symbol=<s>&periodType=year&period=20&frequencyType=daily` returns Schwab's `{"candles": [{"datetime": <epoch ms>, "open", "high", "low", "close", "volume"}, ...]}`. About 5,000 bars per symbol. It needs no secret.
- A daily candle is stamped **00:00 Central of its session day** (06:00 UTC in winter, 05:00 UTC in summer).
- `$ADVN`, `$DECN`, `$TRIN`, `$UVOL`, `$DVOL` carry **weekend-dated junk bars** (18 of 5,021 in `$ADVN`: Friday's value repeated on Saturday and Sunday). `$SPX` carries none. **A session is a day `$SPX` has a bar.**
- `$TRIN` has 128 bars with a close of 0.
- The raw scale of `$UVOL` / `$DVOL` wanders day to day; their **share** is stable. Reference values, up volume ÷ (up + down): 2026-11-23 `0.8229`, 2026-11-24 `0.6171`, 2026-11-25 `0.7820` (from Schwab's bars; TradingView read 0.8247, 0.6192, 0.7831).
- The daily history ends at the **previous** session. At 22:00 CT on 2026-11-27 the last bar was 2026-11-25.
- The live `$UVOL` quote returned `92233720368.54776` (the 64-bit ceiling ÷ 1e8) on a strong up day. The live quote of `$UVOL` / `$DVOL` is never read.
- The raw `/quotes` answer is `{symbol: {"quote": {"lastPrice", "highPrice", "lowPrice", "closePrice", ...}}}`. For an index, `lastPrice` after the close is the session's close and `closePrice` is the PRIOR session's.
- `cache:market:new_highs` holds `{"enabled": True, "session": "<YYYY-MM-DD>", "universe": <int>, "counts": {"52w": {"highs", "lows"}, "13w": {...}, "4w": {...}}}`. It settles `after_close_min` (10) minutes after the close.

## Review focus

Each line has its test in the task named.

1. **Weekend-dated and holiday junk bars in Schwab's breadth history** must not become rows (Task 3, `test_history_rows_keep_only_spx_sessions`).
2. **A zero, non-finite or clipped value** (`$TRIN` 0, `$UVOL` at the 64-bit ceiling) is absent, never a stored 0 and never an up share of about 1.0 (Task 3, `test_unusable_values_are_absent`).
3. **A restart after the settle time, or a second run of the close row,** must not overwrite a value the morning correction already wrote (Task 2, `test_a_close_quote_never_replaces_a_history_value`; Task 4, `test_close_row_is_not_due_when_the_day_is_stored`).
4. **A half day:** the close row is due after the EARLY close plus the settle, and not before it (Task 4, `test_close_row_follows_the_early_close`).
5. **The new highs view is from another session, switched off or absent:** the Schwab rows are still written and the own-count rows are left out (Task 3, `test_stale_new_highs_view_leaves_own_counts_out`).

---

## File structure

| File | Responsibility |
|---|---|
| `config/breadth.toml` (create) | The operator's settings for the daily row and the backfill |
| `shared/breadth_config.py` (create) | Loader: defaults, cleaning, never raises |
| `repo_paths.py` (modify, beside `NEW_HIGHS_TOML` and `IV_HISTORY_DB`) | `BREADTH_TOML`, `BREADTH_DB` |
| `webgui/config_schema.py` (modify, after `_NEW_HIGHS`) | The catalogue entry |
| `shared/breadth_history.py` (create) | The store: schema, vocabulary, the one write path, reads |
| `services/market_svc/compute.py` (modify, after `fetch_bars`) | `fetch_daily`: one daily-history request through the proxy |
| `services/market_svc/breadth_daily.py` (create) | Pure row builders; `due_close`, `due_correction`, `refresh` |
| `services/market_svc/scheduler.py` (modify `loop`) | Run `breadth_daily.refresh` beside the poll |
| `tools/breadth_backfill.py` (create) | CLI: `--source schwab`, `--source csv`, `--status` |
| Tests | `shared/tests/test_breadth_config.py`, `shared/tests/test_breadth_history.py`, `services/market_svc/tests/test_breadth_daily.py`, `tools/tests/test_breadth_backfill.py` |
| Docs | `docs/CHANGELOG.md`, `CLAUDE.md` (folder map row, config list), `docs/reference/config-files.md` |

`BREADTH_DB = SHARED / "data" / "breadth.db"`. `shared/data/` is already gitignored, already in the backup's `DATA_TREES`, and already refused by the suite's `sqlite3.connect` guard, so a new store there needs none of those three edits.

---

### Task 1: Settings

**Files:**
- Create: `config/breadth.toml`, `shared/breadth_config.py`, `shared/tests/test_breadth_config.py`
- Modify: `repo_paths.py`, `webgui/config_schema.py`

**Interfaces:**
- Produces: `repo_paths.BREADTH_TOML`, `repo_paths.BREADTH_DB`; `shared.breadth_config.load() -> dict`, `DEFAULTS`, `reset_cache()`.

`load()` returns exactly these keys (the TOML file ships the same values, each with a comment in plain words):

| Key | Default | Bounds | Meaning |
|---|---|---|---|
| `enabled` | `True` | bool | Write the daily row and its correction |
| `settle_min` | `20` | 11 to 120 | Minutes after the close (the early close on a half day) for the close row. Above the new highs settle (10). |
| `correct_at` | `"07:00"` | `HH:MM` | Central time from which the previous session's row is corrected from Schwab's history |
| `correct_until` | `"12:00"` | `HH:MM`, after `correct_at` | Stop retrying the correction after this |
| `retry_min` | `30` | 5 to 240 | Wait this long after a failed close row or correction |
| `tolerance_pct` | `2.0` | 0.1 to 50 | A correction that moves a value by more than this is counted as a degrade |
| `min_issues` | `1000` | 0 to 5000 | Advancers + decliners below this at the close is "no reading" |
| `history_years` | `20` | 1 to 20 | Years the Schwab backfill asks for |
| `timeout_sec` | `20` | 5 to 120 | Seconds one history request may wait |

Model the loader on `shared/new_highs_config.py` (`toml_loader(BREADTH_TOML, DEFAULTS, label="breadth.toml")`, a cleaner per kind). `correct_until` not after `correct_at` falls both back to their defaults.

- [ ] **Step 1: Write the failing tests** in `shared/tests/test_breadth_config.py`:

```python
import importlib
import datetime as dt

from shared import breadth_config as bc


def test_shipped_file_equals_the_defaults():
    bc.reset_cache()
    assert bc.load() == bc.DEFAULTS


def test_bad_values_fall_back(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {
        "settle_min": 5, "tolerance_pct": "x", "min_issues": -1,
        "history_years": 99, "enabled": "no"})
    got = bc.load()
    assert got["settle_min"] == 20          # below the new highs settle
    assert got["tolerance_pct"] == 2.0
    assert got["min_issues"] == 1000
    assert got["history_years"] == 20
    assert got["enabled"] is True           # only a real False switches it off


def test_a_window_that_does_not_run_forward_falls_back(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {"correct_at": "13:00", "correct_until": "12:00"})
    got = bc.load()
    assert (got["correct_at"], got["correct_until"]) == ("07:00", "12:00")


def test_times_parse(monkeypatch):
    monkeypatch.setattr(bc, "load_raw", lambda: {"correct_at": "06:30"})
    assert bc.clock(bc.load()["correct_at"]) == dt.time(6, 30)


def test_paths():
    import repo_paths
    assert repo_paths.BREADTH_TOML.name == "breadth.toml"
    assert repo_paths.BREADTH_DB.as_posix().endswith("shared/data/breadth.db")
```

- [ ] **Step 2: Run** `"$PY" -m pytest shared/tests/test_breadth_config.py -v`. Expected: FAIL (`No module named shared.breadth_config`).
- [ ] **Step 3: Implement.** Add the two `repo_paths` constants; write `config/breadth.toml`; write `shared/breadth_config.py` with `DEFAULTS`, `load()`, `reset_cache`, and `clock(text: str) -> datetime.time`.
- [ ] **Step 4: Add the catalogue entry** `_BREADTH = ConfigFile(name="breadth.toml", title="Breadth history", icon="stacked_line_chart", restart=(), ...)` after `_NEW_HIGHS` in `webgui/config_schema.py`, one `Field` per key above (`kind="bool"` / `"int"` / `"time"` / `"float"` as the neighbouring entries use them), and add it to the tuple that lists `_NEW_HIGHS` near the foot of the file. `tolerance_pct` is `kind="float"` with `unit="%"`: it is already in percent, not a fraction. Labels are whole words; the summary: "The daily record of market breadth: advancers and decliners, up and down volume, new highs and lows. Kept forever."
- [ ] **Step 5: Run** the config test and, from `webgui/`, `"$PY" -m pytest tests/test_config_schema.py -v`. Expected: PASS, including `test_every_key_in_the_file_has_a_catalogue_entry` for `breadth.toml`.
- [ ] **Step 6: Commit** `feat(market): breadth history settings`.

---

### Task 2: The store

**Files:**
- Create: `shared/breadth_history.py`, `shared/tests/test_breadth_history.py`

**Interfaces:**
- Consumes: `repo_paths.BREADTH_DB`, `shared.market_calendar.next_trading_day` (exclusive), `shared.numeric.finite`.
- Produces:

```python
SOURCES = ("schwab_history", "schwab_close_quote", "own_count", "tradingview")
SERIES: dict[str, str]            # name -> one-line description; the fixed vocabulary
Row = tuple[str, str, float, str]  # (series, day "YYYY-MM-DD", value, source)

def connect(db_path=None) -> sqlite3.Connection      # creates the file and schema
def locked(day: datetime.date, today: datetime.date) -> bool
def upsert(conn, rows, today: datetime.date, force: bool = False) -> dict
    # {"written": n, "unchanged": n, "locked": n, "outranked": n, "invalid": n}
def day_row(conn, day: str) -> dict[str, tuple[float, str]]        # series -> (value, source)
def series_values(conn, series: str, start: str | None = None, end: str | None = None) -> list[tuple[str, float]]
def coverage(conn) -> list[tuple[str, str, str, int]]              # (series, first_day, last_day, rows), by series
```

Schema: `daily(series TEXT NOT NULL, day TEXT NOT NULL, value REAL NOT NULL, source TEXT NOT NULL, PRIMARY KEY (series, day))`, WAL mode.

`SERIES` (exactly these names):

- Schwab: `nyse_adv`, `nyse_dec`, `nyse_trin`, `nyse_up_share`, `spx_close`, `spx_high`, `spx_low`, `vix_close`, `vix3m_close`, `spy_close`, `spy_volume`, `rsp_close`
- Our own count: `us_universe`, `us_52w_highs`, `us_52w_lows`, `us_13w_highs`, `us_13w_lows`, `us_4w_highs`, `us_4w_lows`
- TradingView (study only): `tv_nyse_adv`, `tv_nyse_dec`, `tv_nyse_up_vol`, `tv_nyse_down_vol`, `tv_pct_above_20`, `tv_pct_above_50`, `tv_pct_above_200`, `tv_sp500_pct_above_50`, `tv_sp500_pct_above_200`, `tv_nyse_52w_highs`, `tv_nyse_52w_lows`, `tv_nyse_3m_highs`, `tv_nyse_3m_lows`, `tv_nyse_1m_highs`, `tv_nyse_1m_lows`, `tv_equity_put_call`

Rules `upsert` enforces, in this order, per row:

1. Unknown series, unknown source, a `tv_` series with a source other than `tradingview`, or `tradingview` on a non-`tv_` series: **raise `ValueError`** (a programming error, not data).
2. A value `finite()` refuses, or a day that is not `YYYY-MM-DD`: counted `invalid`, not written.
3. No stored row: written.
4. Stored value equal (`math.isclose`, `rel_tol=1e-9`) and same source: `unchanged`.
5. **Rank:** a stored `schwab_history` row is replaced only by another `schwab_history` row. Any other incoming source is counted `outranked` and not written. This holds even with `force`.
6. `locked(day, today)` and not `force`: counted `locked`, not written.
7. Otherwise: written.

`locked(day, today)` is `next_trading_day(next_trading_day(day)) < today`: a row more than two sessions old.

- [ ] **Step 1: Write the failing tests** in `shared/tests/test_breadth_history.py`:

```python
import datetime as dt

import pytest

from shared import breadth_history as bh

TODAY = dt.date(2026, 11, 27)        # a Friday; the 26th was Thanksgiving


@pytest.fixture
def conn(tmp_path):
    c = bh.connect(tmp_path / "breadth.db")
    yield c
    c.close()


def test_write_then_read(conn):
    got = bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history"),
                           ("nyse_dec", "2026-11-25", 751.0, "schwab_history")], TODAY)
    assert got == {"written": 2, "unchanged": 0, "locked": 0, "outranked": 0, "invalid": 0}
    assert bh.day_row(conn, "2026-11-25") == {"nyse_adv": (1886.0, "schwab_history"),
                                              "nyse_dec": (751.0, "schwab_history")}
    assert bh.series_values(conn, "nyse_adv") == [("2026-11-25", 1886.0)]


def test_the_same_row_twice_is_unchanged(conn):
    row = [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")]
    bh.upsert(conn, row, TODAY)
    assert bh.upsert(conn, row, TODAY)["unchanged"] == 1


def test_history_corrects_a_close_quote(conn):
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1880.0, "schwab_close_quote")], TODAY)
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], TODAY)
    assert bh.day_row(conn, "2026-11-25")["nyse_adv"] == (1886.0, "schwab_history")


def test_a_close_quote_never_replaces_a_history_value(conn):
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], TODAY)
    got = bh.upsert(conn, [("nyse_adv", "2026-11-25", 1.0, "schwab_close_quote")], TODAY, force=True)
    assert got["outranked"] == 1
    assert bh.day_row(conn, "2026-11-25")["nyse_adv"] == (1886.0, "schwab_history")


def test_an_old_row_is_locked_unless_forced(conn):
    old = [("nyse_adv", "2026-11-20", 500.0, "schwab_history")]
    bh.upsert(conn, old, dt.date(2026, 11, 20))
    changed = [("nyse_adv", "2026-11-20", 501.0, "schwab_history")]
    assert bh.upsert(conn, changed, TODAY)["locked"] == 1
    assert bh.upsert(conn, changed, TODAY, force=True)["written"] == 1


def test_locked_counts_sessions_not_days():
    assert bh.locked(dt.date(2026, 11, 25), TODAY) is False   # Wed -> Fri is one session later
    assert bh.locked(dt.date(2026, 11, 24), TODAY) is False   # two sessions
    assert bh.locked(dt.date(2026, 11, 23), TODAY) is True


def test_an_unusable_value_is_not_stored(conn):
    got = bh.upsert(conn, [("nyse_trin", "2026-11-25", float("nan"), "schwab_history"),
                           ("nyse_trin", "not-a-day", 0.7, "schwab_history")], TODAY)
    assert got["invalid"] == 2 and bh.day_row(conn, "2026-11-25") == {}


@pytest.mark.parametrize("row", [
    ("made_up", "2026-11-25", 1.0, "schwab_history"),
    ("nyse_adv", "2026-11-25", 1.0, "guess"),
    ("tv_pct_above_50", "2026-11-25", 42.5, "schwab_history"),
    ("nyse_adv", "2026-11-25", 1.0, "tradingview"),
])
def test_a_row_outside_the_vocabulary_raises(conn, row):
    with pytest.raises(ValueError):
        bh.upsert(conn, [row], TODAY)


def test_coverage(conn):
    bh.upsert(conn, [("nyse_adv", "2026-11-24", 1415.0, "schwab_history"),
                     ("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], TODAY)
    assert bh.coverage(conn) == [("nyse_adv", "2026-11-24", "2026-11-25", 2)]


def test_the_default_path_is_resolved_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setattr(bh, "DEFAULT_DB_PATH", tmp_path / "elsewhere.db")
    bh.connect().close()
    assert (tmp_path / "elsewhere.db").exists()


def test_the_store_is_never_excluded_from_the_backup():
    import sys
    import repo_paths
    sys.path.insert(0, str(repo_paths.REPO_ROOT / "tools"))
    import backup_local
    rel = repo_paths.BREADTH_DB.relative_to(repo_paths.REPO_ROOT).as_posix()
    assert not any(rel == x or rel.startswith(x + "/") for x in backup_local.DB_EXCLUDE)
```

- [ ] **Step 2: Run** `"$PY" -m pytest shared/tests/test_breadth_history.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `shared/breadth_history.py`.** Imports: stdlib, `repo_paths.BREADTH_DB` (as `DEFAULT_DB_PATH`), `shared.market_calendar`, `shared.numeric.finite`. The module docstring says: kept forever, in the nightly backup, never in `DB_EXCLUDE`; `tv_` series are for the study only and never reach a cache view; why it lives in `shared/` (a service and a tool both write it; the `iv_history` precedent). `upsert` runs in one transaction.
- [ ] **Step 4: Run** the test file. Expected: PASS.
- [ ] **Step 5: Commit** `feat(market): the breadth history store`.

---

### Task 3: Row builders (pure)

**Files:**
- Create: `services/market_svc/breadth_daily.py`, `services/market_svc/tests/test_breadth_daily.py`

**Interfaces:**
- Consumes: `shared.breadth_history.Row`, `shared.numeric.finite`.
- Produces:

```python
HISTORY_SYMBOLS = ("$SPX", "$ADVN", "$DECN", "$TRIN", "$UVOL", "$DVOL",
                   "$VIX", "$VIX3M", "SPY", "RSP")
CLOSE_SYMBOLS = ("$SPX", "$ADVN", "$DECN", "$TRIN", "$VIX", "$VIX3M")
CLIP = 9.0e10            # at or above this a volume is the feed's ceiling, not a reading

def session_day(candle: dict) -> str | None            # "YYYY-MM-DD" in Central, None if unusable
def up_share(up, down) -> float | None                 # up / (up + down)
def up_share_from_trin(adv, dec, trin) -> float | None # r = (adv/dec)/trin; r / (1 + r)
def history_rows(candles: dict[str, list[dict]]) -> list[Row]            # source "schwab_history"
def close_rows(day: str, quotes: dict, new_highs: dict | None, min_issues: int) -> list[Row]
def differences(day: str, history: list[Row], stored: dict, tolerance_pct: float) -> list[dict]
    # [{"series", "was", "now", "pct"}] for stored schwab_close_quote values moved by more than the tolerance
```

Mapping, history (`history_rows`): `$SPX` → `spx_close` / `spx_high` / `spx_low`; `$ADVN` → `nyse_adv`; `$DECN` → `nyse_dec`; `$TRIN` → `nyse_trin`; `$UVOL` + `$DVOL` → `nyse_up_share` (never stored raw); `$VIX` → `vix_close`; `$VIX3M` → `vix3m_close`; `SPY` → `spy_close` + `spy_volume`; `RSP` → `rsp_close`. Only days in the set of `$SPX` session days are kept. A value that is not finite, is `<= 0`, or (for a volume) is `>= CLIP` is absent. With no `$SPX` candles the result is `[]`.

Mapping, close (`close_rows`, all from `quote.lastPrice` except the index range from `highPrice` / `lowPrice`): the same series for the six `CLOSE_SYMBOLS`, source `schwab_close_quote`; `nyse_up_share` from `up_share_from_trin`. `nyse_adv` + `nyse_dec` below `min_issues`: the three NYSE rows and the share are left out. SPY and RSP are **not** in the close row: after the close their `lastPrice` is an after-hours print, so their close and volume arrive with the morning correction. The own-count rows (`us_universe`, `us_52w_highs`, … `us_4w_lows`, source `own_count`) are added only when `new_highs` is a dict with `enabled` true and `session == day`. A count of 0 highs or lows is a real reading and IS stored.

- [ ] **Step 1: Write the failing tests** in `services/market_svc/tests/test_breadth_daily.py`:

```python
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.market_svc import breadth_daily as bd

CT = ZoneInfo("America/Chicago")


def bar(day, close, **extra):
    """A Schwab daily candle: stamped midnight Central of its session."""
    y, m, d = (int(x) for x in day.split("-"))
    ms = int(dt.datetime(y, m, d, tzinfo=CT).timestamp() * 1000)
    return {"datetime": ms, "close": close, "high": extra.get("high", close),
            "low": extra.get("low", close), "volume": extra.get("volume", 0)}


def by_series(rows):
    return {(s, d): v for s, d, v, _ in rows}


def test_session_day_is_the_central_date_in_winter_and_summer():
    assert bd.session_day({"datetime": 1795586400000}) == "2026-11-25"   # 06:00 UTC
    assert bd.session_day(bar("2026-07-01", 1.0)) == "2026-07-01"        # 05:00 UTC
    assert bd.session_day({"datetime": "x"}) is None


def test_up_share_matches_the_measured_days():
    assert bd.up_share(64300268.0, 13841219.0) == pytest.approx(0.8229, abs=1e-4)
    assert bd.up_share(753700.0, 467618.0) == pytest.approx(0.6171, abs=1e-4)
    assert bd.up_share(953043.0, 265676.0) == pytest.approx(0.7820, abs=1e-4)


def test_up_share_from_trin():
    # 2083 advancers, 608 decliners, TRIN 0.38510207 -> up/down volume 8.896
    assert bd.up_share_from_trin(2083, 608, 0.38510207) == pytest.approx(0.8990, abs=1e-3)
    assert bd.up_share_from_trin(2083, 0, 0.4) is None
    assert bd.up_share_from_trin(2083, 608, 0) is None


def test_history_rows_keep_only_spx_sessions():
    candles = {"$SPX": [bar("2006-11-27", 1381.9, high=1400.0, low=1380.0)],
               "$ADVN": [bar("2006-11-25", 1688.0), bar("2006-11-26", 1688.0),
                         bar("2006-11-27", 615.0)]}
    got = by_series(bd.history_rows(candles))
    assert got[("nyse_adv", "2006-11-27")] == 615.0
    assert got[("spx_high", "2006-11-27")] == 1400.0 and got[("spx_low", "2006-11-27")] == 1380.0
    assert not any(day in ("2006-11-25", "2006-11-26") for _, day in got)
    assert {src for *_, src in bd.history_rows(candles)} == {"schwab_history"}


def test_history_rows_without_spx_are_empty():
    assert bd.history_rows({"$ADVN": [bar("2026-11-25", 1886.0)]}) == []


def test_unusable_values_are_absent():
    day = "2026-11-25"
    candles = {"$SPX": [bar(day, 7182.78)], "$TRIN": [bar(day, 0.0)],
               "$UVOL": [bar(day, 92233720368.54776)], "$DVOL": [bar(day, 265676.0)],
               "$VIX": [bar(day, float("nan"))]}
    got = by_series(bd.history_rows(candles))
    assert ("nyse_trin", day) not in got
    assert ("nyse_up_share", day) not in got       # a clipped volume gives no share
    assert ("vix_close", day) not in got


def test_spy_carries_volume_and_rsp_does_not_store_it():
    day = "2026-11-25"
    got = by_series(bd.history_rows({"$SPX": [bar(day, 7182.78)],
                                     "SPY": [bar(day, 716.7, volume=73461190)],
                                     "RSP": [bar(day, 215.74, volume=13723055)]}))
    assert got[("spy_volume", day)] == 73461190 and got[("rsp_close", day)] == 215.74
    assert not any(s == "rsp_volume" for s, _ in got)


def q(last, high=0.0, low=0.0):
    return {"quote": {"lastPrice": last, "highPrice": high, "lowPrice": low, "closePrice": 1.0}}


QUOTES = {"$SPX": q(7297.49, 7301.0, 7190.0), "$ADVN": q(2083.0), "$DECN": q(608.0),
          "$TRIN": q(0.38510207), "$VIX": q(23.81), "$VIX3M": q(23.82)}
VIEW = {"enabled": True, "session": "2026-11-27", "universe": 3426,
        "counts": {"52w": {"highs": 90, "lows": 12}, "13w": {"highs": 200, "lows": 0},
                   "4w": {"highs": 310, "lows": 40}}}


def test_close_rows():
    rows = bd.close_rows("2026-11-27", QUOTES, VIEW, 1000)
    got = by_series(rows)
    assert got[("spx_close", "2026-11-27")] == 7297.49     # lastPrice, never closePrice
    assert got[("spx_low", "2026-11-27")] == 7190.0
    assert got[("nyse_up_share", "2026-11-27")] == pytest.approx(0.8990, abs=1e-3)
    assert got[("us_universe", "2026-11-27")] == 3426
    assert got[("us_13w_lows", "2026-11-27")] == 0          # zero is a real count
    sources = {s: src for s, _, _, src in rows}
    assert sources["nyse_adv"] == "schwab_close_quote" and sources["us_52w_highs"] == "own_count"
    assert not any(s.startswith(("spy_", "rsp_")) for s in sources)


@pytest.mark.parametrize("view", [None, {}, {**VIEW, "session": "2026-11-25"},
                                  {**VIEW, "enabled": False}])
def test_stale_new_highs_view_leaves_own_counts_out(view):
    got = by_series(bd.close_rows("2026-11-27", QUOTES, view, 1000))
    assert ("nyse_adv", "2026-11-27") in got
    assert not any(s.startswith("us_") for s, _ in got)


def test_a_thin_feed_writes_no_nyse_rows():
    thin = {**QUOTES, "$ADVN": q(40.0), "$DECN": q(12.0)}
    got = by_series(bd.close_rows("2026-11-27", thin, VIEW, 1000))
    assert not any(s.startswith("nyse_") for s, _ in got)
    assert ("spx_close", "2026-11-27") in got


def test_differences_report_only_moves_past_the_tolerance():
    stored = {"nyse_adv": (2083.0, "schwab_close_quote"), "nyse_dec": (608.0, "schwab_close_quote"),
              "spx_close": (7297.49, "schwab_history")}
    history = [("nyse_adv", "2026-11-27", 2200.0, "schwab_history"),
               ("nyse_dec", "2026-11-27", 610.0, "schwab_history"),
               ("spx_close", "2026-11-27", 7000.0, "schwab_history")]
    got = bd.differences("2026-11-27", history, stored, 2.0)
    assert [d["series"] for d in got] == ["nyse_adv"]       # 5.6%; dec moved 0.3%; spx was not a quote
    assert got[0]["was"] == 2083.0 and got[0]["now"] == 2200.0
```

- [ ] **Step 2: Run** `"$PY" -m pytest services/market_svc/tests/test_breadth_daily.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement the pure functions** in `services/market_svc/breadth_daily.py`. The docstring states the measured facts each rule answers (weekend junk bars, the clipped `$UVOL`, why SPY and RSP wait for the morning), with the date measured.
- [ ] **Step 4: Run** the test file. Expected: PASS.
- [ ] **Step 5: Commit** `feat(market): breadth rows from Schwab history and the close`.

---

### Task 4: The daily row in the service

**Files:**
- Modify: `services/market_svc/compute.py` (add `fetch_daily` after `fetch_bars`), `services/market_svc/breadth_daily.py`, `services/market_svc/scheduler.py` (`loop`), `services/market_svc/tests/test_breadth_daily.py`, `services/market_svc/tests/test_compute.py`

**Interfaces:**
- Consumes: Task 1 `breadth_config.load()`, `clock()`; Task 2 `connect`, `upsert`, `day_row`; Task 3 builders; `compute.fetch_raw_quotes(syms)`; `shared.market_calendar` (`is_trading_day`, `session_close_on`, `prev_trading_day`: exclusive); `bus.cache_get("cache:market:new_highs")` (an envelope: read `.payload`).
- Produces:

```python
# compute.py
def fetch_daily(symbol, *, period_type="year", period=20, timeout=20.0) -> list | None
    # GET {PROXY_URL}/pricehistory, frequencyType=daily. The candles, or None on any failure. Never raises.

# breadth_daily.py
def due_close(now, stored: bool, state: dict, cfg: dict) -> bool
def due_correction(now, state: dict, cfg: dict) -> str | None     # the day to correct, or None
def refresh(bus, state: dict, now=None, db_path=None) -> bool      # True when it wrote; never raises
```

Rules:

- `due_close`: `cfg["enabled"]`, `now` is on a trading day, `now >= session_close_on(day) + settle_min`, `not stored` (the day already has a `nyse_adv` or `spx_close` row), and `now.timestamp() >= state.get("close_next_at", 0)`.
- `due_correction`: `cfg["enabled"]`, a trading day, `correct_at <= now.time() < correct_until`, `state.get("corrected") != prev day`, past `state.get("correct_next_at", 0)`. Returns `prev_trading_day(today)` as text.
- `refresh`, close branch: one `fetch_raw_quotes(CLOSE_SYMBOLS)`; an empty answer sets `close_next_at = now + retry_min` and calls `_degrade.degraded("market.breadth", detail="close quotes")`. Otherwise `close_rows(...)` and `upsert(..., today=now.date())`.
- `refresh`, correction branch: `fetch_daily(sym, period_type="month", period=1)` for each of `HISTORY_SYMBOLS`, one after another. `history_rows` is filtered to rows whose day is the day being corrected. **No such rows (Schwab has not posted the bar yet): set `correct_next_at = now + retry_min`, write nothing, and do not mark it corrected.** Otherwise compute `differences` against `day_row` BEFORE the upsert, upsert, set `state["corrected"]`, and call `_degrade.degraded("market.breadth", detail=..., exc_info=False)` once when the list is not empty.
- `refresh` opens the store per call and closes it; the whole body is under one `try` that ends in `_degrade.degraded("market.breadth")` and returns `False`.
- `scheduler.loop`: a `breadth_state` dict and a `breadth_fetch` future beside `new_highs_fetch`, submitted with the same "at most one at a time" guard, with a one-line comment saying why it is beside the poll (up to ten history requests).

- [ ] **Step 1: Write the failing tests.** Append to `test_breadth_daily.py`:

```python
from shared import breadth_history as bh
from shared import market_calendar as mc

CFG = {"enabled": True, "settle_min": 20, "correct_at": "07:00", "correct_until": "12:00",
       "retry_min": 30, "tolerance_pct": 2.0, "min_issues": 1000, "history_years": 20,
       "timeout_sec": 20}


def at(day, hhmm):
    y, m, d = (int(x) for x in day.split("-"))
    h, mi = (int(x) for x in hhmm.split(":"))
    return dt.datetime(y, m, d, h, mi, tzinfo=CT)


def test_close_row_is_due_after_the_settle():
    assert bd.due_close(at("2026-11-25", "15:19"), False, {}, CFG) is False
    assert bd.due_close(at("2026-11-25", "15:20"), False, {}, CFG) is True


def test_close_row_follows_the_early_close(monkeypatch):
    early = at("2026-11-27", "12:00")
    monkeypatch.setattr(mc, "session_close_on", lambda d: early)
    assert bd.due_close(at("2026-11-27", "12:19"), False, {}, CFG) is False
    assert bd.due_close(at("2026-11-27", "12:20"), False, {}, CFG) is True


def test_close_row_is_not_due_when_the_day_is_stored():
    assert bd.due_close(at("2026-11-25", "22:00"), True, {}, CFG) is False


def test_close_row_is_not_due_on_a_closed_day_or_when_switched_off():
    assert bd.due_close(at("2026-11-26", "16:00"), False, {}, CFG) is False      # Thanksgiving
    assert bd.due_close(at("2026-11-25", "16:00"), False, {}, {**CFG, "enabled": False}) is False


def test_correction_names_the_previous_session():
    assert bd.due_correction(at("2026-11-27", "07:00"), {}, CFG) == "2026-11-25"
    assert bd.due_correction(at("2026-11-27", "06:59"), {}, CFG) is None
    assert bd.due_correction(at("2026-11-27", "12:00"), {}, CFG) is None
    assert bd.due_correction(at("2026-11-27", "07:05"), {"corrected": "2026-11-25"}, CFG) is None


class _Bus:
    def __init__(self, view): self.view = view
    def cache_get(self, key):
        return type("Env", (), {"payload": self.view})() if self.view is not None else None


@pytest.fixture
def wired(monkeypatch, tmp_path):
    from shared import breadth_config
    monkeypatch.setattr(breadth_config, "load", lambda: dict(CFG))
    return tmp_path / "breadth.db"


def test_refresh_writes_the_close_row_once(monkeypatch, wired):
    calls = []
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", lambda syms, **k: calls.append(syms) or QUOTES)
    now = at("2026-11-27", "15:30")
    assert bd.refresh(_Bus(VIEW), {}, now, db_path=wired) is True
    assert bd.refresh(_Bus(VIEW), {}, now, db_path=wired) is False     # a restart: the day is stored
    assert len(calls) == 1
    conn = bh.connect(wired)
    assert conn and bh.day_row(conn, "2026-11-27")["us_universe"] == (3426.0, "own_count")
    conn.close()


def test_refresh_backs_off_when_the_quotes_fail(monkeypatch, wired):
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", lambda syms, **k: {})
    state, now = {}, at("2026-11-27", "15:30")
    assert bd.refresh(_Bus(VIEW), state, now, db_path=wired) is False
    assert state["close_next_at"] == now.timestamp() + 30 * 60


def test_correction_waits_for_schwab_to_post_the_bar(monkeypatch, wired):
    old = {"$SPX": [bar("2026-11-24", 7201.09)], "$ADVN": [bar("2026-11-24", 1415.0)]}
    monkeypatch.setattr(bd.compute, "fetch_daily", lambda sym, **k: old.get(sym, []))
    state, now = {}, at("2026-11-27", "07:10")
    assert bd.refresh(_Bus(None), state, now, db_path=wired) is False
    assert "corrected" not in state and state["correct_next_at"] == now.timestamp() + 30 * 60


def test_correction_replaces_the_close_quote(monkeypatch, wired):
    conn = bh.connect(wired)
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1880.0, "schwab_close_quote")], dt.date(2026, 11, 25))
    conn.close()
    posted = {"$SPX": [bar("2026-11-25", 7182.78)], "$ADVN": [bar("2026-11-25", 1886.0)],
              "SPY": [bar("2026-11-25", 716.7, volume=73461190)]}
    monkeypatch.setattr(bd.compute, "fetch_daily", lambda sym, **k: posted.get(sym, []))
    state = {}
    assert bd.refresh(_Bus(None), state, at("2026-11-27", "07:10"), db_path=wired) is True
    conn = bh.connect(wired)
    row = bh.day_row(conn, "2026-11-25")
    conn.close()
    assert row["nyse_adv"] == (1886.0, "schwab_history") and row["spy_volume"][0] == 73461190
    assert state["corrected"] == "2026-11-25"


def test_refresh_never_raises(monkeypatch, wired):
    def boom(*a, **k): raise RuntimeError("proxy down")
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", boom)
    assert bd.refresh(_Bus(VIEW), {}, at("2026-11-27", "15:30"), db_path=wired) is False


def test_the_scheduler_runs_it_beside_the_poll():
    import inspect
    from services.market_svc import scheduler
    assert "breadth_daily.refresh" in inspect.getsource(scheduler.loop)
```

These tests replace attributes on modules, so `breadth_daily.py` must reach them
through the module: `from shared import market_calendar as mc`,
`from shared import breadth_config`, `from services.market_svc import compute`,
and call `mc.session_close_on(...)`, `breadth_config.load()`,
`compute.fetch_raw_quotes(...)`. `now` is an aware Central datetime; the default
is `datetime.now(ZoneInfo("America/Chicago"))`, as in `new_highs.refresh`.

Append to `services/market_svc/tests/test_compute.py`, following that file's existing fake-session pattern for `fetch_bars`:

```python
def test_fetch_daily_asks_the_proxy_for_daily_candles(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        def json(self): return {"candles": [{"datetime": 1, "close": 2.0}]}

    class _Session:
        def get(self, url, params=None, timeout=None):
            seen.update(url=url, params=params, timeout=timeout)
            return _Resp()

    monkeypatch.setattr(compute, "_SESSION", _Session())
    assert compute.fetch_daily("$ADVN", period_type="month", period=1) == [{"datetime": 1, "close": 2.0}]
    assert seen["url"].endswith("/pricehistory")
    assert seen["params"] == {"symbol": "$ADVN", "periodType": "month", "period": 1,
                              "frequencyType": "daily", "frequency": 1}


def test_fetch_daily_returns_none_on_failure(monkeypatch):
    class _Session:
        def get(self, *a, **k): raise OSError("down")
    monkeypatch.setattr(compute, "_SESSION", _Session())
    assert compute.fetch_daily("$ADVN") is None
```

- [ ] **Step 2: Run** `"$PY" -m pytest services/market_svc -v`. Expected: the new tests FAIL (`due_close`, `refresh`, `fetch_daily` missing); every existing test still passes.
- [ ] **Step 3: Implement** `compute.fetch_daily`, then `due_close`, `due_correction` and `refresh` in `breadth_daily.py` (import `compute` at module level as `from services.market_svc import compute`, so the tests' `bd.compute` exists), then the two lines in `scheduler.loop`.
- [ ] **Step 4: Run** `"$PY" -m pytest services/market_svc -v`. Expected: PASS, the whole suite.
- [ ] **Step 5: Run** `"$PY" -m pytest services/tests/test_no_silent_degrades.py -v`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(market): write the day's breadth row after the close and correct it next morning`.

---

### Task 5: The backfill tool, and the docs

**Files:**
- Create: `tools/breadth_backfill.py`, `tools/tests/test_breadth_backfill.py`
- Modify: `docs/CHANGELOG.md`, `CLAUDE.md`, `docs/reference/config-files.md`

**Interfaces:**
- Consumes: Task 2 store; Task 3 `history_rows`, `HISTORY_SYMBOLS`; Task 4 `compute.fetch_daily`.
- Produces:

```python
def backfill_schwab(conn, fetch, years: int, today, force=False) -> dict     # upsert's counts
def read_csv(path, series: str) -> list[Row]                                  # source "tradingview"
def status_lines(conn) -> list[str]
def main(argv=None) -> int
```

```
.venv/bin/python -X utf8 tools/breadth_backfill.py --source schwab [--years 20] [--force] [--db PATH]
.venv/bin/python -X utf8 tools/breadth_backfill.py --source csv --series tv_pct_above_50 FILE [--force] [--db PATH]
.venv/bin/python -X utf8 tools/breadth_backfill.py --status [--db PATH]
```

- `backfill_schwab` calls `fetch(symbol, period_type="year", period=years)` for each of `HISTORY_SYMBOLS`. **`$SPX` returning `None` or `[]` aborts with a `RuntimeError` and writes nothing** (without the session calendar every junk bar would look valid). Any other symbol failing is skipped and named in the printed summary.
- `read_csv`: header `day,value`; a series not starting `tv_` raises `ValueError`; a blank or non-numeric value is skipped; for a series ending `_highs` or `_lows` the value `0.01` becomes `0.0` (TradingView stores a zero count as 0.01).
- A first load needs no `--force`: the lock only refuses to CHANGE a row that already exists. `--force` is for a re-load that changes old values. The summary prints every count `upsert` returns, so a re-load that changed nothing because rows were locked is visible.
- `--status` prints one line per series: name, first day, last day, rows.
- The tool's docstring carries the three commands above, the rule that a TradingView CSV never enters git, and the design link.

- [ ] **Step 1: Write the failing tests** in `tools/tests/test_breadth_backfill.py` (import the tool as the neighbouring tool tests do; reuse a local `bar()` like Task 3's):

```python
def test_schwab_backfill_writes_history(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    data = {"$SPX": [bar("2026-11-24", 7201.09), bar("2026-11-25", 7182.78)],
            "$ADVN": [bar("2026-11-24", 1415.0), bar("2026-11-25", 1886.0)],
            "$UVOL": [bar("2026-11-25", 953043.0)], "$DVOL": [bar("2026-11-25", 265676.0)]}
    got = tool.backfill_schwab(conn, lambda s, **k: data.get(s), 20, dt.date(2026, 11, 27))
    # $SPX: close, high and low on two days (6); advancers on two days (2); one up share (1)
    assert got["written"] == 9 and got["invalid"] == 0
    assert bh.series_values(conn, "nyse_up_share") == [("2026-11-25", pytest.approx(0.7820, abs=1e-4))]


def test_schwab_backfill_asks_for_the_years_given(tmp_path):
    asked = []
    def fetch(s, **k):
        asked.append((s, k)); return [bar("2026-11-25", 1.0)]
    tool.backfill_schwab(bh.connect(tmp_path / "b.db"), fetch, 7, dt.date(2026, 11, 27))
    assert asked[0] == ("$SPX", {"period_type": "year", "period": 7})
    assert [s for s, _ in asked] == list(bd.HISTORY_SYMBOLS)


def test_schwab_backfill_refuses_to_run_without_the_index(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    with pytest.raises(RuntimeError):
        tool.backfill_schwab(conn, lambda s, **k: None if s == "$SPX" else [bar("2026-11-25", 1.0)],
                             20, dt.date(2026, 11, 27))
    assert bh.coverage(conn) == []


def test_read_csv(tmp_path):
    f = tmp_path / "lows.csv"
    f.write_text("day,value\n2020-03-23,0.01\n2020-03-24,412\n2020-03-25,\n2020-03-26,x\n", encoding="utf-8")
    assert tool.read_csv(f, "tv_nyse_3m_lows") == [
        ("tv_nyse_3m_lows", "2020-03-23", 0.0, "tradingview"),
        ("tv_nyse_3m_lows", "2020-03-24", 412.0, "tradingview")]


def test_read_csv_keeps_a_small_ratio(tmp_path):
    f = tmp_path / "pc.csv"
    f.write_text("day,value\n2020-03-23,0.01\n", encoding="utf-8")
    assert tool.read_csv(f, "tv_equity_put_call")[0][2] == 0.01     # only counts get the zero rule


def test_read_csv_refuses_a_series_that_is_not_tradingviews(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("day,value\n2020-03-23,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        tool.read_csv(f, "nyse_adv")


def test_status_lists_each_series(tmp_path):
    conn = bh.connect(tmp_path / "b.db")
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], dt.date(2026, 11, 27))
    assert tool.status_lines(conn) == ["nyse_adv  2026-11-25  2026-11-25  1"]


def test_main_loads_a_csv(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("day,value\n2020-03-23,4.2\n", encoding="utf-8")
    db = tmp_path / "b.db"
    assert tool.main(["--source", "csv", "--series", "tv_pct_above_50", str(f), "--db", str(db)]) == 0
    conn = bh.connect(db)
    assert bh.series_values(conn, "tv_pct_above_50") == [("2020-03-23", 4.2)]
```

- [ ] **Step 2: Run** `"$PY" -m pytest tools/tests/test_breadth_backfill.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `tools/breadth_backfill.py`** on the shape of `tools/archive_gex.py` (repo root on `sys.path`, `argparse`, `main(argv) -> int`). `--source schwab` passes `compute.fetch_daily` (imported inside `main`, so the tests never build a proxy session) and `breadth_config.load()["history_years"]` when `--years` is not given.
- [ ] **Step 4: Run** `"$PY" -m pytest tools/tests/test_breadth_backfill.py shared/tests/test_breadth_history.py -v`. Expected: PASS.
- [ ] **Step 5: Write the docs.**
  - `docs/CHANGELOG.md`: a new top entry, "**Breadth history: a daily record of market breadth, kept forever.**" What it stores and from where, the two daily writes and why (Schwab's history runs a session behind), the tool's three commands, that no page reads it yet, and the design link.
  - `CLAUDE.md`, folder map: a row for `shared/data/breadth.db` in the style of the `gex_archive.db` row ("**Kept forever and in the nightly backup** … never add it to `DB_EXCLUDE`", the `tv_` rule in one clause, the design link). `CLAUDE.md`, "The files:" list under Paths: add `breadth`. Keep both additions to the minimum: `tests/test_claude_md_size.py` must still pass.
  - `docs/reference/config-files.md`: an entry for `config/breadth.toml` beside `new_highs`.
- [ ] **Step 6: Run** `"$PY" -m pytest tests/test_claude_md_size.py tests/test_ci_covers_every_suite.py -v`. Expected: PASS.
- [ ] **Step 7: Commit** `feat(market): breadth backfill tool; docs`.

---

### Task 6: Load the history on prod (the operator's steps; no code)

There is no dev environment. The store is new and the tool only reads from Schwab, so this is verified on prod after the promote. Each step names its check.

- [ ] **Step 1: Full suites green in the worktree.** `"$PY" -m pytest shared/tests`, `"$PY" -m pytest services/market_svc`, `"$PY" -m pytest tools/tests`, and from `webgui/` `"$PY" -m pytest tests/test_config_schema.py`. Compare the failing SET with `main`, not a count.
- [ ] **Step 2: Merge to `main`, push, promote** (the operator; outside the session window, since the promote stops the whole stack):

```bash
ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
```

- [ ] **Step 3: Backfill from Schwab** (about ten requests, under a minute):

```bash
ssh vps2 'cd /home/administrator/dev && .venv/bin/python -X utf8 tools/breadth_backfill.py --source schwab'
```

Expected: a summary with `written` near 60,000, `invalid` 0 (unusable bars are dropped before the store sees them), no symbol named as failed.

- [ ] **Step 4: Check the coverage and the reference days.**

```bash
ssh vps2 'cd /home/administrator/dev && .venv/bin/python -X utf8 tools/breadth_backfill.py --status'
```

Expected: every Schwab series starts in late November 2006 (`vix3m_close` in November 2007) and ends on the last closed session. Then confirm `nyse_up_share` reads 0.8229, 0.6171 and 0.7820 (±0.001) on 2026-11-23, -24 and -25, and that no series has a row dated 2006-11-25 or 2006-11-26.

- [ ] **Step 5: Confirm the two daily writes on the next session.** After the close plus 20 minutes, `--status` shows that day as the last day of `nyse_adv` and `us_52w_highs`. The following morning after 07:00 CT, `spy_volume` has the day too, and the Status card's degrade count for `market.breadth` is 0 (or carries the tolerance message, which is then read and judged).
- [ ] **Step 6: The TradingView load.** **Blocked on the operator's answer to the design's open question 4 (TradingView's terms).** When cleared: a Claude session pulls one series per `get-ohlcv` call (16 series; spread over two days to stay inside the connector's daily quota), writes each as `day,value` with the day taken in `America/New_York`, the files are copied to prod with one `scp`, and each is loaded with `--source csv --series <name> <file>`. Check: `--status` lists the 16 `tv_` series, and `tv_nyse_adv` agrees with `nyse_adv` on the three reference days. The CSV files are deleted from the checkout afterwards.

---

## What this slice deliberately leaves out

- The cycle engine, `cache:market:cycle`, the Desk panel, the weather season and the reports (design slices 2 to 4).
- SPY and RSP in the close row. Their after-hours `lastPrice` is not the session's close; whether the quote's regular-session field is reliable is a question for slice 2, which is the first reader that needs today's volume.
- A source for the share of stocks above an average (the design's options A and B). The study decides whether one is needed.
- Any page, view or alert for the store. Its health is the degrade count and `--status`.
