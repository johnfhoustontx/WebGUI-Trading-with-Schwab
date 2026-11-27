# New highs / new lows on the Macro Board — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Counts of US-listed stocks at a new 52 / 13 / 4-week high and low, from TradingView's screener, in the Macro Board's control bar beside Advancing / Declining.

**Architecture:** A new `services/market_svc/new_highs.py` asks `scanner.tradingview.com` for thirteen `totalCount`s (one universe count, then window × side × row) on its own cadence beside the 3-second poll, and publishes `cache:market:new_highs` (validated by `MarketNewHighs`). `webgui/pages/market.py` reads that view and draws a two-row, three-window group in the control bar, in-app and on the public `/macro`.

**Tech Stack:** Python 3.11, `requests` through `shared/http_watchdog.py`, pydantic contracts, NiceGUI, pytest + fakeredis.

**Spec:** [docs/plans/2026-11-27-new-highs-lows-design.md](2026-11-27-new-highs-lows-design.md)

`PY` below is `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (the worktree has no venv of its own). Service and shared suites run from the repo root; webgui suites from `webgui/`.

## Global Constraints

- Universe: `type = stock`, `is_primary = true`, `exchange` in `["NYSE", "NASDAQ", "AMEX"]`; sent as `{"left": "exchange", "operation": "in_range", "right": [...]}`.
- New high: `{"left": "high", "operation": "egreater", "right": <window high field>}`; new low: `{"left": "low", "operation": "eless", "right": <window low field>}`.
- Windows, in this order: `52w` "52 wk" `price_52_week_high`/`price_52_week_low`; `13w` "13 wk" `High.3M`/`Low.3M`; `4w` "4 wk" `High.1M`/`Low.1M`. Code, not config.
- The $5 row adds `{"left": "close", "operation": "egreater", "right": min_price}`; `min_price = 0` means no `over5` row at all.
- Request body otherwise: `{"markets": ["america"], "symbols": {"query": {"types": []}}, "columns": ["name"], "range": [0, 1]}`; only `totalCount` is read.
- Requests one after another, never in parallel; a generic browser `User-Agent`; nothing about the operator in any request.
- A refresh publishes all counts or nothing. Absence is a dash, never 0.
- Cadence: every `rth_refresh_sec` (60) from the regular open to `session_close_on(day)`; one refresh at `session_close_on(day) + after_close_min` (10); nothing before the open, nothing on a closed day.
- No Schwab, proxy or Claude call.
- `skip_unchanged=True` on publish, so the view carries NO timestamp of its own.
- Tailwind classes only on the page; colours from `_T['MB_UP']` / `_T['MB_DN']`.
- Config read on every refresh (no restart); every key in `webgui/config_schema.py`.

## Review Focus

1. **TradingView answers 200 with a body that is not the shape we expect** (an error object, `totalCount` as a string or negative). Expected: that count is `None` and nothing is published. Test in Task 3.
2. **One of thirteen requests fails mid-refresh.** Expected: no publish, the previous view stays, degrade counted, retry after `retry_sec`. Test in Task 3.
3. **Half-day session (2026-11-27 closes 12:00 CT).** Expected: refreshing stops at the early close and the after-close refresh lands at 12:10, not 15:10. Test in Task 3.
4. **The view is from a previous session** (Monday morning before the open shows Friday's). Expected: the group greys and says "as of the close". Test in Task 4.
5. **`enabled = false` then back on.** Expected: one `enabled: false` publish hides the group; switching on refreshes at once. Test in Task 3.

---

### Task 1: Configuration

**Files:**
- Create: `config/new_highs.toml`, `shared/new_highs_config.py`, `shared/tests/test_new_highs_config.py`
- Modify: `repo_paths.py:179` (add `NEW_HIGHS_TOML = REPO_ROOT / "config" / "new_highs.toml"` after `ETF_HOLDINGS_TOML`), `webgui/config_schema.py` (a `_NEW_HIGHS` `ConfigFile` after `_ETF_HOLDINGS`, registered wherever `_ETF_HOLDINGS` is)

**Interfaces:**
- Produces: `shared.new_highs_config.load() -> dict` with keys `enabled: bool`, `url: str`, `exchanges: list[str]`, `min_price: float`, `rth_refresh_sec: int`, `after_close_min: int`, `timeout_sec: int`, `retry_sec: int`, `public_show: bool` (from `[public] show`). `DEFAULTS` dict, `reset_cache()`.

- [ ] **Step 1: Write the failing tests** in `shared/tests/test_new_highs_config.py`:
  - `test_defaults_when_file_missing`: monkeypatch `load_raw` to return `{}`; `load()` equals `{"enabled": True, "url": "https://scanner.tradingview.com/america/scan", "exchanges": ["NYSE", "NASDAQ", "AMEX"], "min_price": 5.0, "rth_refresh_sec": 60, "after_close_min": 10, "timeout_sec": 10, "retry_sec": 120, "public_show": True}`.
  - `test_bad_values_fall_back`: `rth_refresh_sec = 0` → 60; `rth_refresh_sec = 5` → 60 (floor 15); `min_price = -1` → 5.0; `exchanges = ["NYSE", 3, ""]` → `["NYSE"]`; `exchanges = []` → the default list; `url = "ftp://x"` → the default (must start `https://`); `public = {"show": "yes"}` → `True` (non-bool ignored).
  - `test_min_price_zero_is_kept`: `min_price = 0` → `0.0`.
- [ ] **Step 2: Run** `PY -m pytest shared/tests/test_new_highs_config.py -v` — Expected: FAIL, module not found.
- [ ] **Step 3: Implement** `shared/new_highs_config.py` on `toml_loader(NEW_HIGHS_TOML, DEFAULTS, label="new_highs.toml")`, shaped like `shared/etf_holdings_config.py` (`_whole`-style clamps: `rth_refresh_sec` 15..3600, `after_close_min` 0..120, `timeout_sec` 2..60, `retry_sec` 15..3600, `min_price` 0..1000). Write `config/new_highs.toml` with the design's keys and a header comment saying: the source is TradingView's unofficial screener address; 13 wk is 3 months and 4 wk is 1 month; read on every refresh.
- [ ] **Step 4: Add the schema entry**: title "New highs / lows", icon `trending_up`, `restart=()`, caution "TradingView's screener address is unofficial: it can change or stop answering without notice." One Section with a Field per key (`public.show` labelled "Show on the public Macro Board").
- [ ] **Step 5: Run** `PY -m pytest shared/tests/test_new_highs_config.py -v` and `cd webgui && PY -m pytest tests/test_config_schema.py -q` — Expected: PASS.
- [ ] **Step 6: Commit** `feat(market): new highs / lows configuration`.

### Task 2: The contract and the pure builders

**Files:**
- Create: `services/market_svc/new_highs.py`, `services/market_svc/tests/test_new_highs.py`
- Modify: `shared/contracts/market.py` (add `MarketNewHighs`), `shared/contracts/tests/test_market.py`

**Interfaces:**
- Consumes: `new_highs_config.load()` keys (Task 1).
- Produces:
  - `MarketNewHighs(_Base)`: `enabled: bool = True`, `source: str = ""`, `universe: int = 0`, `session: str = ""`, `min_price: float = 0`, `exchanges: list[str] = []`, `public: bool = True`, `rows: dict = {}`. A `field_validator("rows")` rejects any count that is not a non-negative `int` (bool excluded) at `rows[row][window][side]`.
  - `new_highs.WINDOWS: tuple[tuple[str, str, str, str], ...]` — `(key, label, high_field, low_field)` as in Global Constraints.
  - `new_highs.requests_for(cfg: dict) -> list[tuple[str, dict]]` — first `("universe", body)`, then `f"{row}.{window}.{side}"` for row in `all` (+ `over5` when `min_price > 0`), window in `WINDOWS` order, side in `highs`, `lows`.
  - `new_highs.build(counts: dict[str, int | None], cfg: dict, session: str) -> dict | None`.
  - `new_highs.CACHE = "cache:market:new_highs"`, `EVENT = "events:market:new_highs"`, `publish(bus, payload) -> int` (`MarketNewHighs(**payload).model_dump()`, `skip_unchanged=True`).

- [ ] **Step 1: Write the failing tests**:
  - `test_requests_for_order_and_count`: with `min_price=5`, 13 keys, first `"universe"`, second `"all.52w.highs"`, last `"over5.4w.lows"`; with `min_price=0`, 7 keys and none starting `over5`.
  - `test_universe_body_has_only_the_universe_filter`: its `filter` is exactly the three universe items; `range == [0, 1]`; `columns == ["name"]`.
  - `test_window_bodies`: `all.13w.lows` filter ends with `{"left": "low", "operation": "eless", "right": "Low.3M"}` and has no `close` item; `over5.52w.highs` contains `{"left": "close", "operation": "egreater", "right": 5.0}` and ends with the `price_52_week_high` comparison.
  - `test_build_full`: counts for all 13 keys → `rows["all"]["52w"] == {"highs": 121, "lows": 85}`, `universe == 4984`, `session == "2026-11-27"`, `source == "TradingView"`, `public` from `cfg["public_show"]`, and no key named `ts` anywhere.
  - `test_build_any_none_builds_nothing`: one value `None` → `None`.
  - Contract (`shared/contracts/tests/test_market.py`): `test_new_highs_rejects_bad_counts` — `-1`, `1.5`, `"3"`, `True` each raise `ValidationError`; a full valid payload round-trips.
- [ ] **Step 2: Run** `PY -m pytest services/market_svc/tests/test_new_highs.py shared/contracts/tests/test_market.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the contract and the three functions plus `publish`. `new_highs.py` imports nothing from `compute`.
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `feat(market): new highs / lows view and its contract`.

### Task 3: Fetching, the refresh and the scheduler

**Files:**
- Modify: `shared/http_watchdog.py` (add `post(url, **kwargs)`, the twin of `get`), `services/market_svc/new_highs.py`, `services/market_svc/scheduler.py` (`loop`), `services/market_svc/tests/test_new_highs.py`

**Interfaces:**
- Consumes: Task 2's functions.
- Produces:
  - `new_highs._post(url, **kwargs)` — the seam tests replace; calls `http_watchdog.post`.
  - `new_highs.fetch_count(body: dict, cfg: dict) -> int | None` — POST `json=body`, `timeout=cfg["timeout_sec"]`, under a `Watch` armed for `DEADLINE_FACTOR × timeout` as `news_svc/fetch.py` does; `None` on a raised exception, non-200, unparseable JSON, or a `totalCount` that is not a non-negative int.
  - `new_highs.due(now: datetime, state: dict, cfg: dict) -> bool` — PURE, per the cadence in Global Constraints. `state` keys used: `next_at` (unix), `after_close_done` (date string).
  - `new_highs.refresh(bus, state, now=None, wall=None) -> bool` — never raises; True when it published.

- [ ] **Step 1: Write the failing tests** (`monkeypatch.setattr(new_highs, "_post", fake)`; `fake` returns an object with `status_code` and `json()`):
  - `test_fetch_count_ok`: `{"totalCount": 121, "data": []}` → 121.
  - `test_fetch_count_failures` (parametrised): status 500; `json()` raises `ValueError`; `{"error": "x"}`; `{"totalCount": "121"}`; `{"totalCount": -1}`; `_post` raises `requests.ConnectionError` → each `None`.
  - `test_due_cadence`: on 2026-11-24 (a Tuesday) 08:00 CT → False; 08:31 with empty state → True; 08:31:30 with `next_at` 60 s later → False; 15:05 → False; 15:10 → True once, then False with `after_close_done` set; Saturday 2026-11-28 10:00 → False; Thanksgiving 2026-11-26 10:00 → False.
  - `test_due_half_day`: 2026-11-27 11:59 CT → True; 12:05 → False; 12:10 → True (after-close).
  - `test_refresh_publishes_all_or_nothing`: a fake answering every body → one publish, `Bus().cache_get("market:new_highs")` holds 13-key-derived rows; then a fake failing on the 7th call → no new version, `_degrade` counted (monkeypatch `_degrade.degraded` to record `"market.new_highs"`), `state["next_at"]` is `wall + retry_sec`.
  - `test_refresh_requests_are_sequential`: the fake records call order; 13 calls in `requests_for` order.
  - `test_refresh_disabled_then_enabled`: `enabled=False` → one publish of `{"enabled": False, ...}`, second call publishes nothing; back to `True` → publishes on the next call regardless of `next_at`.
  - `test_refresh_never_raises`: `load` raising → returns False, degrade recorded.
- [ ] **Step 2: Run** `PY -m pytest services/market_svc/tests/test_new_highs.py -v` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement** `post`, `fetch_count`, `due`, `refresh`. In-session test uses `mc.is_trading_day(d)`, `mc.regular_open_on(d) <= now < mc.session_close_on(d)` (not `is_regular_hours`, which does not know half days). `session` is `now.date().isoformat()`.
- [ ] **Step 4: Wire the scheduler**: in `scheduler.loop`, a `new_highs_state: dict = {}` and `new_highs_fetch = None`, started like `holdings_fetch` (at most one at a time, `run_in_executor(None, new_highs.refresh, bus, new_highs_state)`), and add it to the module docstring's list of side fetches.
- [ ] **Step 5: Run** `PY -m pytest services/market_svc -q` — Expected: all pass (compare the failing set with `main`: empty).
- [ ] **Step 6: Commit** `feat(market): refresh new highs / lows from TradingView's screener`.

### Task 4: The Macro Board group

**Files:**
- Modify: `webgui/pages/market.py`, `webgui/tests/test_market.py`

**Interfaces:**
- Consumes: the published view shape (Task 2).
- Produces:
  - `market.NEW_HIGHS_VIEW = "market:new_highs"`.
  - `market.new_highs_visible(view, public: bool) -> bool` — False for a non-dict, `enabled is False`, or `public and view.get("public") is False`.
  - `market.new_highs_cells(view, today: str) -> dict` — `{"rows": [(row_label, [(high_text, low_text), …three windows]), …], "labels": ["52 wk", "13 wk", "4 wk"], "stale": bool, "hover": str}`. Row labels "All stocks" and "$5 and up" (from `min_price`: a whole number prints as "$5", anything else through `fmt.money`). A missing count is `"–"`. `stale` is `view["session"] != today`. `hover` is "Stocks at a new high / low across {universe:,} {exchanges} stocks, from TradingView's screener. 13 wk is 3 months and 4 wk is 1 month." with exchanges printed "NYSE, NASDAQ and NYSE American".

- [ ] **Step 1: Write the failing tests**:
  - `test_new_highs_cells_full`: the design's sample view → rows `[("All stocks", [("121","85"),("205","120"),("347","158")]), ("$5 and up", [("120","12"),("203","27"),("314","55")])]`, `stale` False for `today="2026-11-27"`, hover contains "4,984" and "NYSE American".
  - `test_new_highs_cells_missing_count_is_dash`: remove `rows.all.4w.lows` → `"–"`.
  - `test_new_highs_cells_no_over5_row`: no `over5` in rows → one row.
  - `test_new_highs_cells_stale`: `today="2026-11-30"` → `stale` True.
  - `test_new_highs_visible` (parametrised): `None` → False; `{"enabled": False}` → False; `public=True` with view `public: False` → False; `public=True` with `public: True` → True; `public=False` with `public: False` → True.
- [ ] **Step 2: Run** `cd webgui && PY -m pytest tests/test_market.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the helpers, then the group in `render()`: after the breadth column and before `ui.space()`, a column holding a header row ("NEW HIGHS / LOWS" in `_LBL` + the three window labels) and one row per `rows` entry, highs in `_T['MB_UP']`, lows in `_T['MB_DN']`, values in `_VAL`, a `ui.tooltip` with `hover`, `opacity-[.62]` plus an "as of the close" label when `stale`. Read with `bus_client.read_full(NEW_HIGHS_VIEW)` at build, and in `_poll` probe `read_version` → `read` off the loop as the holdings view does; repaint label texts in place, and `set_visibility` from `new_highs_visible(view, shell.is_public())` (`import shell`, the seam; never `import main`). `today` is the Central date (`_dt.datetime.now(_CT).date().isoformat()`).
- [ ] **Step 4: Run** `cd webgui && PY -m pytest tests/test_market.py tests/test_no_inline_style.py tests/test_ui_kit_guard.py -q` — Expected: PASS.
- [ ] **Step 5: Check in a browser**: `tools/ui_harness.py` on `/market` with the sample view put on the fake bus; screenshot the control bar; stop the harness.
- [ ] **Step 6: Commit** `feat(market): new highs / lows in the Macro Board's control bar`.

### Task 5: Documentation

**Files:**
- Modify: `webgui/page_help.py` (the Macro Board entry), `docs/manuals/` User Guide, Reference Guide and Technical Reference Macro Board sections, `docs/webgui-routes.md` (`/market`), `docs/CHANGELOG.md`, `CLAUDE.md` (one clause in the `services/` row: market_svc makes one outside call, to TradingView's screener, for the new-high / new-low counts)

- [ ] **Step 1:** Write each: what the counts are, the universe, the windows (13 wk = 3 months, 4 wk = 1 month), the $5 row and why, the cadence (every minute in the session, once after the close), that the source is TradingView's unofficial screener, and the switches (`enabled`, `[public] show`).
- [ ] **Step 2: Run** `PY -m pytest tests/test_claude_md_size.py -q` and `cd webgui && PY -m pytest tests -q -k "page_help or manuals"` — Expected: PASS.
- [ ] **Step 3: Commit** `docs: new highs / lows on the Macro Board`.

### Finally

- [ ] Run every suite the change touches (`services/market_svc`, `shared/tests`, `shared/contracts`, `webgui/tests`) and compare the failing set with `main`'s.
- [ ] Merge to `main` and promote only on the operator's go-ahead (`ssh vps2 'cd /home/administrator/dev && tools/promote.sh'`), outside trading hours; then confirm `cache:market:new_highs` is published on prod and the group shows on `/market` and on `live.`'s `/macro`.
