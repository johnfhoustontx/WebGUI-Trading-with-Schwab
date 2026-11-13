# X Feed inside the app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the standalone Node "X News Feed" into this app as a Tier-2 service `xfeed_svc` plus an **X Feed** tab under Market News (`/news/x`), carry its data over, and retire the standalone feed.

**Architecture:** `services/xfeed_svc` (FastAPI via `services/_scaffold.make_app`, port 8219) owns a SQLite store with the Node app's schema unchanged, polls FxEmbed on a scheduler, publishes `cache:xfeed:latest` and `cache:xfeed:accounts`, and answers `cmd:xfeed` commands into per-request keys `cache:xfeed:answer:{request_id}`. The webgui page reads those views, sends commands through `bus_client.request`, and renders in the app's style.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, `requests`, FastAPI/uvicorn (scaffold), Redis via `shared.bus` (fakeredis under pytest), NiceGUI + Tailwind (`pages/ui_kit.py`), pytest.

**Spec:** `docs/plans/2026-11-13-x-feed-design.md` (read it first). Behavioural source being ported: the Node app at `D:\X-Posts` (`src/*.js`, `public/app.js`, `test/*.test.js`, `test/fixtures/*.json`, `docs/superpowers/specs/2026-11-10-x-news-feed-design.md`).

## Global Constraints

- Service name `xfeed_svc`, domain `xfeed`, port **8219** (8218 / `feed_svc` are reserved). Route `/news/x`, tab label **X Feed**; `/news` keeps its route, tab label **Headlines**.
- Tier 1 (`webgui/`) imports nothing new: no `sqlite3`, no `requests`, no `services.*`. Only `nicegui`, `bus_client`, existing `pages.*` helpers.
- Every X string reaches the page through `ui.label` / `ui.link` — never `ui.html`. Every URL from X passes `pages.news_view.safe_href`.
- Store schema identical to `D:\X-Posts\src\db.js` `SCHEMA` (tables `accounts`, `posts`, `gaps`, `settings`; same columns; times in epoch **milliseconds**; JSON text columns `cashtags`, `media`, `quote`, `counts`, `raw`).
- Every tunable lives in `config/xfeed.toml`, read through `shared.config_toml.toml_loader`, with a `webgui/config_schema.py` entry (STANDING RULE). No `ts` in published payloads.
- Times shown on the page are Central (`America/Chicago`), as `pages/news.py` does.
- Tailwind classes only (no `.style(...)`), `pages/ui_kit.py` components (no raw `ui.button` / `ui.notify` / `ui.table`), handlers wrapped in `ui_guard`.
- Python for tests, from the worktree root: `PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (a worktree has no venv). Service tests: `"$PY" -m pytest services/xfeed_svc -q`. Webgui tests: `(cd webgui && "$PY" -m pytest tests/<file> -q)`.
- Commits pass the pre-commit lint gate (`tools/lint_gate.py`); never `--no-verify`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Nothing touches prod until Task 12, and Task 12's prod steps run only when the user has agreed the timing.

## Review Focus

1. **A migrated account whose `last_ok_at` is hours old** must come back as caught-up rows at their real times, never NEW and never a flood — the first cycle after every service start is a forced catch-up. (Task 5 `test_first_cycle_after_start_is_catchup`.)
2. **Two browser tabs querying at the same moment** must each get their own rows. (Task 6 `test_two_queries_get_separate_answer_keys`.)
3. **Adding an account while a long catch-up is polling another account** must not wait for the whole cycle. (Task 5 `test_add_does_not_wait_for_other_accounts_poll`.)
4. **Hostile X text** (`<script>`, `javascript:` links, a 2,000-char post) must render as inert text and never become a link. (Task 9 `test_split_text_never_links_unsafe_urls`, `test_row_line_truncates_and_strips_newlines`.)
5. **A command dropped by a service restart or replay limit** must answer the page ("try again") rather than leave a spinner. (Task 6 `test_dropped_command_answers_page`.)

---

### Task 1: Service registration, config and empty app

**Files:**
- Modify: `config/ports.toml` (`[services]`: `xfeed = 8219   # X accounts feed (xfeed_svc)`)
- Modify: `repo_paths.py:262` area (add `XFEED_SVC_DATA = REPO_ROOT / "services" / "xfeed_svc" / "data"`, `XFEED_DB = XFEED_SVC_DATA / "xfeed.db"`)
- Modify: `.gitignore` (add `services/xfeed_svc/data/` beside `services/news_svc/data/`)
- Modify: `conftest.py` `_LIVE_DIRS` (add `_ROOT / "services" / "xfeed_svc" / "data"`)
- Create: `config/xfeed.toml`, `services/xfeed_svc/__init__.py`, `services/xfeed_svc/config.py`, `services/xfeed_svc/app.py`, `services/xfeed_svc/tests/__init__.py`, `services/xfeed_svc/tests/conftest.py` (copy of `services/news_svc/tests/conftest.py` with `parents[3]`)
- Modify: `webgui/pages/status.py` `svc_labels` (`"xfeed": "xfeed_svc (X accounts feed)"`), `webgui/tests/test_status.py` expected key sets
- Modify: `.github/workflows/ci.yml` (row `xfeed_svc` → `python -m pytest services/xfeed_svc -q`), `tests/test_ci_covers_every_suite.py` `ROOT_SUITES` (+ `"services/xfeed_svc"`)
- Modify: `webgui/config_schema.py` (`XFEED = "xfeed_svc"`, label `"X feed service"`, a `_XFEED = ConfigFile(name="xfeed.toml", title="X feed", icon="alternate_email", restart=(), ...)` with a `Field` per key below, appended to `FILES`)
- Test: `services/xfeed_svc/tests/test_config.py`, `services/xfeed_svc/tests/test_app.py`

**Interfaces:**
- Produces: `services.xfeed_svc.config.DEFAULTS: dict`, `load() -> dict`, `reset() -> None` (from `toml_loader(REPO_ROOT / "config" / "xfeed.toml", DEFAULTS, label="xfeed.toml")`); `services.xfeed_svc.app.app` (FastAPI).

`config/xfeed.toml` sections and defaults (the keys every later task reads):

```toml
[fxembed]
base_url = "https://api.fxtwitter.com"
user_agent = "neuralstrike-xfeed/1.0"
timeout_s = 15
not_found_retries = 3
retry_delay_s = 0.5

[poller]
interval_s = 60          # minimum 30, enforced in code
account_gap_s = 1.0
page_gap_s = 1.0
max_pages = 25
backoff_s = [60, 120, 300]
not_found_limit = 3
live_slack_min = 15      # OLD_POST_SLACK
recent_factor = 3        # "live" needs last_ok within 3 intervals
tick_s = 5

[views]
latest_rows = 300
query_limit_max = 1000
top_tickers = 10
answer_ttl_s = 120
group_name_max = 30

[page]
new_highlight_s = 60
answer_poll_s = 0.25
answer_timeout_s = 20
```

`restart=()` because the scheduler re-reads config every tick; verify `config_schema`'s restart helpers accept an empty tuple, else use `(XFEED,)`.

- [ ] **Step 1: Write the failing tests.** `test_config.py::test_defaults_load_without_file` (monkeypatch the path to a missing file → `load()["poller"]["interval_s"] == 60`, `["views"]["latest_rows"] == 300`); `test_tracked_file_matches_defaults` (loading the real `config/xfeed.toml` equals `DEFAULTS`). `test_app.py::test_health_answers` (`TestClient(app).get("/health").status_code == 200`, as `services/news_svc/tests/test_app.py` does). Run `webgui/tests/test_config_schema.py` and `webgui/tests/test_status.py` too.
- [ ] **Step 2: Run them** — `"$PY" -m pytest services/xfeed_svc -q` → FAIL (module missing).
- [ ] **Step 3: Implement** the files above. `app.py` mirrors `services/news_svc/app.py` with `make_app("xfeed")` and no scheduler or handler yet (Task 7 wires them).
- [ ] **Step 4: Run** `"$PY" -m pytest services/xfeed_svc tests/test_ci_covers_every_suite.py -q` and `(cd webgui && "$PY" -m pytest tests/test_config_schema.py tests/test_status.py -q)` → PASS.
- [ ] **Step 5: Commit** `feat(xfeed): register xfeed_svc (port 8219), its config and an empty app`.

---

### Task 2: Handle normalisation and status mapping

**Files:**
- Create: `services/xfeed_svc/mapping.py`, `services/xfeed_svc/tests/fixtures/` (copy `D:\X-Posts\test\fixtures\*.json` verbatim)
- Test: `services/xfeed_svc/tests/test_mapping.py`

**Interfaces:**
- Produces:
  - `normalize_handle(text) -> str | None` — `D:\X-Posts\src\handle.js` exactly (same regex, same reserved set).
  - `extract_cashtags(text) -> list[str]` — `src/mapStatus.js` `CASHTAG` regex, uppercase, unique, first-appearance order, trailing dots stripped.
  - `map_status(status: dict, watched: str) -> dict | None` — returns a **post dict** with keys `status_id, account_handle, type, text, author_name, author_handle, author_avatar, url, created_at (ms), cashtags, media, quote, counts, raw`; `media` items `{type, url, thumb, width, height, alt}`; `quote` `{author_name, author_handle, text, url}` or `None`; `counts` `{replies, reposts, likes, views}`. Rules as `mapStatus.js`: repost if `reposted_by`; other people's tweets → `None`; replies only to self (`type="reply"`), else `None`.
  - `PostDict` is this shape everywhere below (store adds `first_seen_at, sort_at, origin`).

- [ ] **Step 1: Write the failing tests** — one pytest per test in `D:\X-Posts\test\handle.test.js` (2) and `test\mapStatus.test.js` (8), same scenarios and fixtures, names in snake_case, keys in snake_case.
- [ ] **Step 2: Run** `"$PY" -m pytest services/xfeed_svc/tests/test_mapping.py -q` → FAIL.
- [ ] **Step 3: Implement** `mapping.py`.
- [ ] **Step 4: Run** → PASS (10 tests).
- [ ] **Step 5: Commit** `feat(xfeed): handle normalisation and FxEmbed status mapping`.

---

### Task 3: FxEmbed client

**Files:**
- Create: `services/xfeed_svc/fxapi.py`
- Test: `services/xfeed_svc/tests/test_fxapi.py`

**Interfaces:**
- Consumes: `config.load()["fxembed"]`.
- Produces: `class FxClient(cfg: dict, *, session=None, sleep=time.sleep)` with `fetch_statuses(handle: str, *, cursor: str | None = None, with_replies: bool = True) -> dict`, returning exactly one of `{"status": "ok", "results": list, "cursor_bottom": str | None}`, `{"status": "not_found"}`, `{"status": "error", "error": str}`. `session` is anything with `.get(url, headers=, timeout=)` returning a `requests.Response`-like object (tests pass a fake); default `requests.Session()`.

Behaviour as `src/fxapi.js`: URL `{base}/2/profile/{quote(handle)}/statuses` with `with_replies=1` and/or `cursor=` query; 204 → ok empty; 404 or body `code == 404` → not_found, retried `not_found_retries` times with `retry_delay_s` sleeps before believing it; 429 → `"rate limited"`; other non-2xx → `"HTTP {n}"`; bad JSON → `"bad response from FxEmbed"`; body `code` not 200 → `"API {code}"`; timeout → `"timeout"`; connection error → `"network: {msg}"`. Total-deadline guard as in `services/news_svc/fetch.py` (a stuck socket cannot outlive ~3× `timeout_s`). Never raises.

- [ ] **Step 1: Write the failing tests** — one per test in `D:\X-Posts\test\fxapi.test.js` (11), with a fake session and a recording `sleep`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `fxapi.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(xfeed): FxEmbed client with false-404 retries`.

---

### Task 4: Store

**Files:**
- Create: `services/xfeed_svc/store.py`
- Test: `services/xfeed_svc/tests/test_store.py`

**Interfaces:**
- Consumes: `repo_paths.XFEED_DB`, `PostDict` (Task 2).
- Produces: `class Store(db_path=None)` (`None` → `repo_paths.XFEED_DB` read at call time; `":memory:"` allowed). Every method holds one `threading.RLock`; connection `check_same_thread=False`, `isolation_level=None`, `PRAGMA busy_timeout`, `PRAGMA journal_mode=WAL` (not for `:memory:`), writes in `BEGIN IMMEDIATE … COMMIT`. `_migrate()` runs the Node `SCHEMA` (`CREATE … IF NOT EXISTS`). Methods (snake_case ports of `src/db.js`, same SQL semantics):
  - `upsert_account(handle, *, display_name, avatar_url, now, group_name=None) -> None` (reactivate; `group_name=None` keeps the existing group)
  - `set_account_group(handle, group_name) -> None`
  - `get_account(handle) -> dict | None`; `list_accounts(*, active_only=False) -> list[dict]` (ordered `added_at, handle`). **Account dict** keys: `handle, display_name, avatar_url, active (bool), added_at, baseline_at, last_checked_at, last_ok_at, last_error, fail_count, not_found_streak, next_check_at, group_name`.
  - `deactivate_account(handle) -> None`
  - `record_check(handle, *, now, ok, error=None, not_found_streak=0, next_check_at=0) -> None`
  - `seen_ids(handle, ids) -> set[str]`
  - `insert_posts(posts: list[dict]) -> int` (INSERT OR IGNORE; JSON-encodes the JSON columns)
  - `get_posts(handle, ids) -> list[dict]` (feed rows, newest first)
  - `insert_gap(*, handle, from_at, to_at, now) -> dict`
  - `query_feed(*, account=None, group=None, ungrouped=False, type=None, q=None, ticker=None, before=None, before_key=None, limit=300) -> list[dict]` — exactly `queryFeed` incl. the `before_key = "status_id:account_handle"` tiebreak and gap merging only when no `type/q/ticker`.
  - `account_counts() -> dict[str, int]` (visible posts per active account)
  - `close() -> None`
  - **Feed row** shapes: post `{kind: "post", account_handle, status_id, first_seen_at, created_at, sort_at, origin, type, text, author_name, author_handle, author_avatar, url, cashtags, media, quote, counts}` (no `raw`); gap `{kind: "gap", id, account_handle, from_at, to_at, sort_at}`.

- [ ] **Step 1: Write the failing tests** — one per test in `D:\X-Posts\test\db.test.js` (16), plus `test_opens_a_node_created_db` (create a DB with the Node `SCHEMA` text via raw `sqlite3` in `tmp_path`, insert one row of each table, open with `Store(path)` → rows read back with identical values) and `test_concurrent_threads_do_not_error` (two threads each `insert_posts` 200 rows into one `Store` → 400 rows, no exception).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `store.py`.
- [ ] **Step 4: Run** → PASS (18 tests).
- [ ] **Step 5: Commit** `feat(xfeed): SQLite store with the Node feed's schema`.

---

### Task 5: Poller

**Files:**
- Create: `services/xfeed_svc/poller.py`
- Test: `services/xfeed_svc/tests/test_poller.py`

**Interfaces:**
- Consumes: `Store` (Task 4), `FxClient` (Task 3), `map_status` (Task 2), `config.load()["poller"]`.
- Produces:
  - `backoff_ms(fail_count: int, steps_s=(60, 120, 300)) -> int`
  - `assign_sort_times(items: list[dict], *, live: bool, first_seen_at: int, last_ok_at: int | None) -> list[int]`
  - `take_baseline(store, fx, handle, *, now, group_name=None) -> dict` → `{"ok": True, "account": dict}` or `{"ok": False, "reason": "not_found" | str}`
  - `poll_account(store, fx, account, *, now, cfg, sleep=time.sleep, force_catchup=False) -> dict` → `{"new_posts": list[feed row], "gap": dict | None, "status": "ok" | "not_found" | "error"}`; `cfg` is the `[poller]` section; `max_pages`, `page_gap_s`, `not_found_limit`, `live_slack_min`, `recent_factor`, `backoff_s` come from it.
  - `class Poller(store, fx, *, load_cfg=config.load, now_ms=<epoch-ms clock>, sleep=time.sleep)`:
    - `run_cycle(on_change: Callable[[], None]) -> None` — polls due active accounts one after another, `account_gap_s` apart; first poll of each account since this `Poller` was created, or while `fail_count > 0`, uses `force_catchup=True`; calls `on_change()` after each account; one failing account never stops the cycle (logged).
    - `add_account(handle, group_name) -> dict` — `take_baseline` under that handle's lock, then marks it polled-since-start.
    - `account_lock(handle) -> threading.Lock` — one lock per handle; `run_cycle` holds an account's lock only while polling that account.
    - `interval_s() -> int` — `max(30, cfg interval_s)`.

Algorithm: port `src/poller.js` line for line (frontier rule, known-at-bottom stop rule, plain-timeline fallback on page 1 only, gap on not-done, hidden baseline rows, live/catch-up split, sort times). Do not "improve" it.

- [ ] **Step 1: Write the failing tests** — one per test in `D:\X-Posts\test\poller.test.js` (26), using a fake `fx` scripted by handle/cursor as `test/helpers.js` does, plus:
  - `test_first_cycle_after_start_is_catchup` — an account with `last_ok_at = now - 3h` and two unseen originals created after it → after `Poller(...).run_cycle`, both rows have `origin == "catchup"` and `sort_at == created_at`; none `"live"`.
  - `test_add_does_not_wait_for_other_accounts_poll` — hold `poller.account_lock("a")` in another thread; `poller.add_account("b", None)` returns `{"ok": True, ...}` within 1 s.
  - `test_interval_floor` — `interval_s = 5` in cfg → `interval_s() == 30`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `poller.py`.
- [ ] **Step 4: Run** → PASS (29 tests).
- [ ] **Step 5: Commit** `feat(xfeed): port the poller (baseline, catch-up, gaps, live rule)`.

---

### Task 6: Commands, answers and published views

**Files:**
- Create: `services/xfeed_svc/runtime.py`, `services/xfeed_svc/handlers.py`
- Test: `services/xfeed_svc/tests/test_handlers.py`

**Interfaces:**
- Consumes: `Store`, `FxClient`, `Poller`, `normalize_handle`, `config.load`.
- Produces:
  - `runtime.get() -> Runtime` (lazy singleton: `Runtime(store, fx, poller)` on default paths); `runtime.set_for_tests(rt | None)`.
  - Constants in `handlers.py`: `CACHE_LATEST = "cache:xfeed:latest"`, `EVENT_LATEST = "events:xfeed:latest"`, `CACHE_ACCOUNTS = "cache:xfeed:accounts"`, `EVENT_ACCOUNTS = "events:xfeed:accounts"`, `answer_key(request_id) -> "cache:xfeed:answer:{request_id}"`, `COMMANDS = ("add_account", "remove_account", "set_group", "query")`, `SLOW = frozenset({"add_account"})`.
  - `handle_command(bus, command) -> None` — dispatch on `command.type`; args in `command.args`; `request_id` must match `^[0-9a-f]{32}$` or the command is ignored (logged).
  - `on_dropped(bus, command, why) -> None` — answers `{"ok": False, "error": "The X feed service restarted - try again."}` when the command had a valid `request_id`.
  - `publish_latest(bus, rt) -> int`, `publish_accounts(bus, rt) -> int`, `publish_all(bus, rt) -> None` — `cache_set(..., skip_unchanged=True)`.
  - `resolve_group(store, text, max_len) -> str | None` — trim, collapse spaces, empty → `None`, longer than `group_name_max` → `ValueError("Group names can be at most 30 characters")`, reuse an existing group's spelling case-insensitively.
  - `top_tickers(rows, n) -> list[{"tag": str, "n": int}]` (count desc, tag asc).

Payloads (exact keys):
- `latest`: `{"rows": [feed rows from query_feed(limit=latest_rows)], "tickers": top_tickers(rows), "total": len(rows)}`
- `accounts`: `{"accounts": [account dicts, active only], "counts": account_counts(), "interval_s": int, "page": cfg["page"]}`
- answers (always `request_id`, `command`, `ok`, `error` — `error` is `""` when ok):
  - `add_account` → + `"account"`; errors `"Invalid handle"`, `"Already watching @{h}"`, `"Account not found"`, `"Could not reach FxEmbed ({reason})"`, the group-length message.
  - `remove_account` → errors `"Not watching that account"`.
  - `set_group` → + `"account"`; same not-watching error.
  - `query` → + `"rows"`, `"tickers"`, `"counts"`; `limit` clamped to `[1, query_limit_max]`.
- Every answer is written with `ttl=answer_ttl_s` and no event. After a successful add/remove/set_group, `publish_all`.

- [ ] **Step 1: Write the failing tests** with `Bus(fake=True)` after `reset_fake_bus()` (as `services/news_svc/tests/test_econ_calendar.py:102`), a `Store(":memory:")`, a scripted fake fx, `runtime.set_for_tests(...)`. Port the behavioural tests of `D:\X-Posts\test\server.test.js` (all except static files, SSE, bad-JSON and 415 — those were HTTP concerns) as command tests, plus:
  - `test_two_queries_get_separate_answer_keys` — two `query` commands with different `request_id`s and different `group`s → both `answer_key`s exist with their own rows.
  - `test_dropped_command_answers_page` — `on_dropped(bus, cmd, "restart")` → answer has `ok is False` and the restart message.
  - `test_empty_store_publishes_empty_views` — `publish_all` on a fresh store → `latest.rows == []`, `accounts.accounts == []`.
  - `test_answers_expire` — the answer key has a TTL ≤ `answer_ttl_s` (`bus._r.ttl(key)` > 0).
  - `test_bad_request_id_is_ignored` — `request_id="../x"` writes no key.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `runtime.py`, `handlers.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(xfeed): commands, per-request answers, latest and accounts views`.

---

### Task 7: Scheduler and app wiring

**Files:**
- Create: `services/xfeed_svc/scheduler.py`
- Modify: `services/xfeed_svc/app.py`, `services/tests/test_scaffold.py:511` (add `"xfeed_svc"` to the parametrize list)
- Test: `services/xfeed_svc/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `runtime.get()`, `handlers.publish_all`, `Poller.run_cycle`, `Poller.interval_s`, `services._heartbeat`.
- Produces: `async def loop(bus) -> None`; module seams `_sleep = asyncio.sleep`, `_monotonic = time.monotonic`. `app = make_app("xfeed", scheduler=scheduler.loop, command_handler=handlers.handle_command, slow_commands=handlers.SLOW, on_dropped=handlers.on_dropped)`.

Loop: publish all views once at start (so an empty store shows "No accounts yet", not "waiting"); then every `tick_s`: `_heartbeat.tick()` inside the `while` body, re-read config, and if no cycle task is running and `interval_s()` has passed since the last cycle **ended** (or none has run), start `run_cycle(on_change=lambda: publish_all(bus, rt))` in the default executor as a background task. A running cycle is never doubled. Cancellation cancels and awaits the cycle task (pattern: `services/news_svc/scheduler.py` `loop`'s `finally`).

- [ ] **Step 1: Write the failing tests** with fake `_sleep` / `_monotonic` (never patch `asyncio.sleep` globally): `test_publishes_views_at_start`, `test_first_cycle_runs_immediately`, `test_next_cycle_waits_interval_from_end`, `test_running_cycle_is_not_doubled`, `test_config_interval_change_applies_without_restart`.
- [ ] **Step 2: Run** `"$PY" -m pytest services/xfeed_svc services/tests/test_scaffold.py -q` → FAIL.
- [ ] **Step 3: Implement** `scheduler.py`, wire `app.py`.
- [ ] **Step 4: Run** the same → PASS.
- [ ] **Step 5: Commit** `feat(xfeed): scheduler loop and app wiring`.

---

### Task 8: Data import tool

**Files:**
- Create: `tools/import_x_feed.py`
- Test: `tools/tests/test_import_x_feed.py`

**Interfaces:**
- Consumes: `services.xfeed_svc.store.Store` (to create the target schema), `repo_paths.XFEED_DB`.
- Produces: `main(argv=None) -> int`; `import_db(source: Path, target: Path, *, force: bool = False) -> dict[str, int]` returning per-table row counts. CLI: `python tools/import_x_feed.py --source ~/x-news-feed/data/feed.db [--target <path>] [--force]`.

Behaviour: open the source **read-only** and take a consistent copy with `sqlite3.Connection.backup` into a temp file (includes the WAL); refuse (exit 2, message) if the target already has any `accounts` row unless `--force` (which empties the four tables first); copy all rows of `accounts`, `posts`, `gaps`, `settings` column-for-column in one transaction; verify per-table counts equal the source's; print `accounts=N posts=N gaps=N settings=N`; exit 0. Never writes to the source.

- [ ] **Step 1: Write the failing tests**: `test_imports_every_row_identically` (fixture source built with the Node schema: 2 accounts incl. one inactive, posts of all three origins, 1 gap, 1 setting → identical rows in target), `test_refuses_non_empty_target`, `test_force_replaces`, `test_source_is_untouched` (source file bytes/mtime unchanged).
- [ ] **Step 2: Run** `"$PY" -m pytest tools/tests/test_import_x_feed.py -q` → FAIL.
- [ ] **Step 3: Implement** `tools/import_x_feed.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(xfeed): one-shot import of the standalone feed's database`.

---

### Task 9: Page view model (pure)

**Files:**
- Create: `webgui/pages/xfeed_view.py`
- Test: `webgui/tests/test_xfeed_view.py`

**Interfaces:**
- Consumes: payload shapes from Task 6; `pages.news_view.safe_href`.
- Produces (pure, no NiceGUI):
  - `VIEW_LATEST = "xfeed:latest"`, `VIEW_ACCOUNTS = "xfeed:accounts"`, `answer_view(request_id) -> "xfeed:answer:{request_id}"`.
  - `row_line(row: dict, *, now_ms: int, new_highlight_s: int) -> dict` → `{"time": "HH:MM" (CT, from sort_at), "type_word": "POST"|"RT"|"REPLY", "text": first line, newlines collapsed, ≤ 240 chars with "…", "(media)"/"(quote)"/"(no text)" fallbacks as app.js, "photos": int, "videos": int, "has_quote": bool, "chips": first 2 cashtags, "more": int, "handle": "@x", "is_new": origin == "live" and now_ms - first_seen_at < new_highlight_s*1000}`.
  - `with_dividers(rows: list[dict]) -> list[tuple[str, object]]` → `("day", "Thu 13 Nov")` whenever the CT date of `sort_at` changes, `("row", row)` or `("gap", row)` otherwise.
  - `gap_text(gap) -> str` → `"Some posts from @x between HH:MM and HH:MM may be missing"` (CT).
  - `split_text(text: str) -> list[dict]` → segments `{"kind": "text"|"url"|"mention"|"cashtag", "text": str, "href": str | None}`; `url` href only when `safe_href` accepts it (else the segment is `text`); mention href `https://x.com/{name}`; cashtag href `None` (the page wires it).
  - `symbol_href(tag) -> "/symbol?symbol={TAG}"`.
  - `is_filtered(state: dict) -> bool` and `query_args(state: dict, *, before_row: dict | None = None, limit: int) -> dict` — `state` keys `group` (`None`=All, `""`=Ungrouped, else name), `account`, `type`, `q`, `ticker`; a `$TICKER` search becomes `ticker`; `before_row` sets `before`/`before_key = "{status_id}:{account_handle}"`.
  - `group_tabs(accounts_view: dict) -> list[dict]` → `[{"key": None, "label": "All", "count": n}, …one per group in first-seen order…, {"key": "", "label": "Ungrouped", "count": n}]`; `[]` when no account has a group; Ungrouped tab only if some account has none.
  - `accounts_by_group(accounts_view) -> list[tuple[str, list[dict]]]`.
  - `checked_text(account, now_ms) -> tuple[str, str]` → `("checked 12s ago", "ok")` or `(last_error, "error")`.
  - `status_text(accounts_view, *, stale: bool) -> tuple[str, str]` → `("Live · checking every 60s", "ok")`; `("X feed service not answering", "error")` when `stale`.

- [ ] **Step 1: Write the failing tests**: one per function above with real values, plus
  - `test_split_text_never_links_unsafe_urls` — `"<script>x</script> javascript:alert(1) https://ok.example/a"` → the script stays a `text` segment, `javascript:` is `text`, only `https://ok.example/a` is a `url` with that href.
  - `test_row_line_truncates_and_strips_newlines` — 2,000-char text with `\n` → no `\n`, length ≤ 241.
  - `test_new_only_for_live_within_window` — live at now-30s → new; live at now-90s → not; catchup at now-1s → not.
  - `test_dollar_search_becomes_ticker` — `q="$nvda"` → `{"ticker": "NVDA"}` and no `q`.
- [ ] **Step 2: Run** `(cd webgui && "$PY" -m pytest tests/test_xfeed_view.py -q)` → FAIL.
- [ ] **Step 3: Implement** `xfeed_view.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(xfeed): pure view model for the X Feed page`.

---

### Task 10: The X Feed page and the Market News tab group

**Files:**
- Create: `webgui/pages/xfeed.py`
- Modify: `webgui/main.py` — add `MARKET_NEWS_CHILDREN = [("/news", "Headlines", "newspaper"), ("/news/x", "X Feed", "alternate_email")]`; remove `("/news", "Market News", "newspaper")` from `OPTIONS_RAIL`; append `("Market News", "newspaper", MARKET_NEWS_CHILDREN)` to `_NAV_GROUPS` (append, never insert — positional reads); in `NAV_SECTIONS` MARKETS replace `_sec_page("/news")` with `_sec_group("Market News")`; add `MARKET_NEWS_CHILDREN` to `_NAV_LABEL`'s sources; `_TAB_COLOR["/news/x"]` (a hue distinct from the map's others); new `@_page("/news/x")` → `with _layout("/news/x", "Market News · X Feed"): from pages import xfeed; xfeed.render()`; change `/news`'s layout title to `"Market News · Headlines"`.
- Modify: `webgui/page_help.py` (`HELP_MD["/news/x"]`), `webgui/tests/test_shell.py` (add `"/news/x"`), `webgui/tests/test_no_inline_style.py` (add `"xfeed.py", "xfeed_view.py"`), `webgui/tests/test_news_page.py` (`/news` is now in `MARKET_NEWS_CHILDREN`, breadcrumb per `breadcrumb_trail`)
- Test: `webgui/tests/test_xfeed_page.py`

**Interfaces:**
- Consumes: Task 9 functions; `bus_client.read`, `bus_client.read_version`, `bus_client.request`; `pages.view_watch.watch_view`; `shell.may_enqueue()`; `pages/ui_kit.py`; `pages/fmt.py`; theme tokens.
- Produces: `render() -> None`.

Page behaviour (spec §Tier 1):
- Header via `kit.header` with the status line from `status_text` (stale = `accounts` view's `{key}:ts` older than 3 × interval, read with `bus_client.read_meta`) and "Updated HH:MM".
- Filter box (collapsible): group tabs, search input (debounced ~400 ms), type toggle All/Posts/Reposts/Replies, account chips with counts, the "most mentioned" strip (click → ticker filter), "N posts shown".
- Feed: `with_dividers` → one-line rows (left border tint for NEW, as `news.py`'s `ROW_ACCENT` does), each row click toggles an expansion with `split_text` segments as `ui.label`/`ui.link`, photos as `ui.image` linking to the full image, video thumb + "Play on X ↗", quote inset, counts, "Posted {CT datetime}", "Open on X ↗". A cashtag in the expansion offers two actions: filter by it, and "Open Symbol page" (`symbol_href`).
- Accounts panel (collapsible; right column ≥ `lg`, below otherwise): add form (handle + group input with existing groups as options), per group heading the accounts with avatar, name, @handle, `checked_text`, a group menu (existing groups / Ungrouped / New group… prompt) and ✕ with a confirm dialog; "Checking every 60s · change in Settings" linking to `/settings`.
- Data flow: unfiltered and nothing older loaded → render `VIEW_LATEST`, repaint on its version (`watch_view`). Filtered or older loaded → send `query`; on `VIEW_LATEST` version change while filtered, re-send the current query. "Load older posts" button at the bottom (and when scrolled near it) sends `query` with `before_row` = last post row and appends.
- Every command: `rid = uuid4().hex`, `bus_client.request("xfeed", {"type": ..., "args": {..., "request_id": rid}})`, then a `ui.timer(page.answer_poll_s)` reading `answer_view(rid)` until it appears or `answer_timeout_s` passes ("The X feed service did not answer - try again."). Errors show inline (add form) or as `kit.toast`. All `request` calls sit behind one `_may_enqueue = _shell.may_enqueue()` check (`test_news_page.py`'s AST test pattern).
- Empty states: no accounts → "No accounts yet — add one in the panel."; no matches → "No posts match these filters."

- [ ] **Step 1: Write the failing tests** in `test_xfeed_page.py`: `test_route_is_in_market_news_group` (`("/news/x", "X Feed", "alternate_email") in main.MARKET_NEWS_CHILDREN`, `"/news"` not in `OPTIONS_RAIL` routes), `test_help_exists`, `test_every_request_is_gated_by_may_enqueue` (AST: all `request` calls inside `if _may_enqueue` blocks), `test_no_ui_html` (source contains no `ui.html(`), `test_imports_stay_on_allow_list` (AST imports ⊆ `{nicegui, bus_client, pages.*, uuid, datetime, time, functools, shell}` — match whatever `pages/news.py` imports), and update `test_news_page.py` / `test_shell.py`.
- [ ] **Step 2: Run** `(cd webgui && "$PY" -m pytest tests/test_xfeed_page.py tests/test_news_page.py tests/test_shell.py tests/test_no_inline_style.py -q)` → FAIL.
- [ ] **Step 3: Implement** `pages/xfeed.py` and the `main.py` / `page_help.py` changes.
- [ ] **Step 4: Run** the whole webgui suite `(cd webgui && "$PY" -m pytest . -q)` → no new failures versus `main` (run it on a clean checkout of `origin/main` first if any fail, and compare the failing SET, not the count).
- [ ] **Step 5: Visual check** with `tools/ui_harness.py` (see `docs/reference/testing.md` / `webgui-dev-notes.md` for its use) on `/news` and `/news/x`, seeding fakeredis with a `latest` payload built from `services/xfeed_svc/tests/fixtures` and an `accounts` payload with 3 groups; screenshot both, at desktop and phone width. Fix layout issues before committing.
- [ ] **Step 6: Commit** `feat(xfeed): X Feed page as a tab of Market News`.

---

### Task 11: Documentation and full verification

**Files:**
- Modify: `docs/CHANGELOG.md` (dated entry: what shipped, pieces, test counts, commit SHAs)
- Modify: `docs/webgui-routes.md` (new `/news/x` section; `/news` now the Headlines tab of the Market News group)
- Modify: `docs/manuals/` — every manual that lists pages, services, ports or commands (grep the manuals for `news_svc` and `8216` and add `xfeed_svc` / `8219` / `cmd:xfeed` beside each), the User Guide's Market News section (the X Feed tab: groups, adding/removing, filters, live, gaps, where the interval is set), and `docs/manuals/diagrams/make_diagrams.py` (the new service box)
- Modify: `CLAUDE.md` — only the service/port list and the 3-tier paragraph's service list (keep within `tests/test_claude_md_size.py`'s cap)
- Modify: `docs/plans/2026-11-13-x-feed-design.md` — Status: approved, built

- [ ] **Step 1:** Make the edits above.
- [ ] **Step 2: Run every suite this change touches**: `"$PY" -m pytest services/xfeed_svc services/tests tools/tests tests -q` and `(cd webgui && "$PY" -m pytest . -q)` → no failures beyond those already on `origin/main` (compare sets).
- [ ] **Step 3: Run** `"$PY" tools/lint_gate.py` over the tree → clean; and pyright if the repo's gate requires it (`docs/reference/testing.md`).
- [ ] **Step 4: Commit** `docs(xfeed): routes, manuals, changelog and CLAUDE.md for the X Feed`.
- [ ] **Step 5: Whole-branch review** (fresh reviewer) before merge.

---

### Task 12: Merge, cut-over and retirement (with the user)

Operational; each prod step only after the user agrees the time. Commands the user must run with sudo are given to them, not run.

- [ ] **Step 1:** Merge `claude/x-feed-in-app` into `main` (fast-forward or merge commit per repo habit), push `origin main`.
- [ ] **Step 2 (agreed time):** `ssh vps2 'cd /home/administrator/dev && tools/promote.sh'` → expect every probe green incl. `:8219/health`. If it rolls back, stop here; the old feed is untouched.
- [ ] **Step 3:** On vps2: `systemctl --user stop trading-prod-xfeed_svc` (confirm the generated unit name with `systemctl --user list-units 'trading-prod-*'`), `systemctl --user stop x-news-feed && systemctl --user disable x-news-feed`.
- [ ] **Step 4:** `cd /home/administrator/dev && .venv/bin/python tools/import_x_feed.py --source ~/x-news-feed/data/feed.db` → counts printed equal the source's (expect ~29 accounts, ~4,150+ posts, 1 gap, 1 setting).
- [ ] **Step 5:** `systemctl --user start trading-prod-xfeed_svc`; `curl -s 127.0.0.1:8219/health`.
- [ ] **Step 6: Verify on app.neuralstrike.co/news/x** (signed in): migrated history and 3 groups visible; first catch-up rows not flagged NEW; add a throwaway account, see it baselined; remove it; filters, ticker strip, load older; a new post arrives live; Status page shows `xfeed_svc` healthy; `/news` unchanged. Record the result in `docs/CHANGELOG.md` (commit, merge, push — docs only, no promote needed).
- [ ] **Step 7 (user OK):** Retire the tailnet serve: `ssh vps2 'tailscale serve status'`, then `tailscale serve --https=4300 off` (with `sudo` if the operator is not set — the user runs it).
- [ ] **Step 8 (user OK):** Caddy: diff `/etc/caddy/Caddyfile` against `/etc/caddy/Caddyfile.pre-feed`; give the user the exact `handle_path /feed/*` block to delete and the commands `sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy`; afterwards confirm `https://cos.neuralstrike.co/feed/` no longer serves the feed and cos-hub still answers.
- [ ] **Step 9 (user OK, after a few days):** remove `~/.config/systemd/user/x-news-feed.service`, `systemctl --user daemon-reload`, archive `~/x-news-feed` to `~/x-news-feed-retired-<date>.tar.gz` (keeping `feed.db`).
