# Raw feed and catch-up — design

**Date:** 2026-10-14 · **Status:** approved in conversation, awaiting spec review ·
**Asked for by the operator:** "the Schwab API writes to a 'raw' database [that]
should be an independent process. The GEX history collector should be collecting
and/or generating GEX and other data from this database. If the production stack
is down and is brought up there should be a catch up mechanism so that intraday
data is not lost or there are no gaps." Earlier the same day: "if I promote to
prod during market hours I need you to keep the gex collector up and running and
not restarted."

## 1. What is true today

- **There is no raw database.** The proxy's market-data store is in memory only
  (`schwab-proxy/market_store.py:8`; a restart starts empty). Persisting raw chains
  was considered and rejected on 2026-10-03 at ~1 GB/day compressed
  (`docs/plans/2026-10-03-market-data-store-design.md`, option B and "No history of
  raw chains is kept").
- **Collection fetches and derives in one step, inside options_svc.**
  `scheduler.gex_due` (1-minute slots, `[windows.collection]` 06:30/08:00–15:20 CT)
  → `handlers.collect_gex_history` → `compute.collect_gex_snapshots` →
  `gex_collector.poll_once`: one 7-day `get_option_chain` per symbol (~92 symbols,
  6 workers, the proxy's priority lane), plus a 10-day `$SPX` term chain every 5
  minutes and an off-hours batched `/quotes` for spot. The chain is computed into
  gex/charm/dex/vanna/prem rows (+ `hiro_minutes`, + `tprem` when on) in
  `options-scanner/gex_history.db`, and then discarded.
- **Riding on that fetch** — the `on_chain` hook (`compute.py` ~4963): the gamma
  stash for `refresh_gamma_current`, ETH eligibility, `flow_sides_tick.on_chain`,
  `traded_premium.on_chain`, `flow_alerts.detect_uoa` / `detect_big_delta`,
  `hiro_tick_row`. After the poll, from the DB: `publish_flow_skew`,
  `run_flow_alerts`, `publish_matrix`, `publish_net_premium`.
- **Nothing backfills a missed minute.** The scheduler's grace windows never
  backfill; HIRO re-seeds after a 150 s gap; `flow_sides` books volume across a gap
  as unlabelled.
- **Every unit is `PartOf=trading-<env>.target`**, the proxy included, and
  `promote.sh` stops the whole target. So every promote during the window costs
  one-minute slots, permanently.

**The hard limit.** Schwab serves no past option chain: OI, IV, volume and greeks
at 10:42 cannot be fetched at 11:00. Underlying bars can. So "no gaps" is only
achievable for data some process SAVED while it happened. The design keeps the
saving process running through a promote and lets everything downstream catch up.

## 2. Decisions (operator, 2026-10-14)

| Question | Answer |
|---|---|
| Which outages must leave no gap? | **Promotes and deriver crashes.** The proxy and the ingest stay up through a promote; anything downstream can stop, crash or be redeployed and catches up. A proxy or ingest crash, or a box reboot, still leaves a gap — made visible, never silent. |
| Raw retention | **5 sessions** (matches `gex_history.db`), ~5 GB, excluded from the nightly backup. Enough to re-derive the whole visible history after a formula fix. |
| Raw contents | **Chains only, first:** the one-minute chains, the 5-minute `$SPX` term chain and the off-hours spot quotes. The streamer's ticks are NOT recorded; the stream-side bought/sold tally keeps today's gap rule. |
| Alerts from caught-up minutes | **Recorded always; pushed only if fresh** (`[catchup] push_max_age_min`, default 3). |
| Approach | **A:** a separate ingest unit (`feed_svc`), a per-session SQLite raw store, a cursor-driven deriver in options_svc, a two-target promote. Rejected: B (the proxy writes raw — a raw-store fix would restart the Schwab sign-in, rate limiter and stream mid-session) and C (Redis Streams — 1 GB/day on a 7 GB box, not backed up, lost on a Redis restart). |

## 3. The ingest — `services/feed_svc` (:8218)

**Job:** fetch, stamp, compress, write. It computes nothing.

- Takes over today's fetch loop unchanged in behaviour: the same universe
  (`gex_collector.collection_symbols()`, the GTH eligible subset), the same window
  and 1-minute cadence, one chain per symbol through the proxy with `X-Priority`,
  the same `collection_tiers` / carry rules (a carried chain is stored with its
  `carried_age_sec`), the 5-minute `$SPX` term chain, the off-hours spot quotes.
- The fetch half of `options-scanner/gex_collector.py` (`_fetch`, tiers, carry,
  spot fix) is split into a module both sides can import; the compute half stays.
  `feed_svc` imports the fetch half, the proxy client, `repo_paths`,
  `shared.market_calendar`, `shared.config_toml` and the raw store — **no engine**.
- Its own minute boundary is the collector's: the minute-floored `ts` it already
  stamps (`gex_collector.py` ~606), so a raw minute and a derived minute share a key.

**Restart:** resumes at the current minute and writes the minutes it missed into
`gaps` with `reason = "feed down"`. It never tries to fetch the past.

**Health:** `/health` reports `last_complete_minute`; inside the window it is
`up: false` once no minute has completed for `[feed] stale_alarm_min` (default 2),
which feeds the existing failed-unit push and the Status card.

## 4. The raw store — `data/raw/<YYYY-MM-DD>.db`

One SQLite file per session (WAL). Path through `repo_paths`; `db_path=None`
resolved at call time (testable).

```sql
CREATE TABLE chains (
    ts          INTEGER NOT NULL,   -- minute-floored unix seconds
    symbol      TEXT    NOT NULL,
    kind        TEXT    NOT NULL,   -- 'chain' | 'term' | 'spot'
    fetched_at  REAL    NOT NULL,
    carried_age_sec REAL,           -- NULL = fetched this minute
    body        BLOB    NOT NULL,   -- zlib(JSON) of the proxy's response
    PRIMARY KEY (ts, symbol, kind)
);
CREATE TABLE minutes (              -- written LAST, in the same transaction
    ts INTEGER PRIMARY KEY, symbols INTEGER, written_at REAL
);
CREATE TABLE gaps (
    ts INTEGER PRIMARY KEY, reason TEXT, noted_at REAL
);
```

- **One transaction per minute**: every chain of that minute, then its `minutes`
  row. A minute is either complete or absent — the deriver reads `minutes`, never
  `chains`, to decide what exists.
- A symbol that failed in an otherwise complete minute is simply missing from
  `chains` for that `ts` (as a failed fetch is missing from `gex_history.db` today).
- **Retention:** at the first write of a session, files older than
  `[feed] keep_sessions` (default 5) are deleted. `data/raw/` is gitignored and
  excluded from the nightly backup (`tools/backup_local.py`'s list is derived from
  `.gitignore` by a test — the exclusion is explicit).
- **Readers open read-only** (`mode=ro` URI). Only `feed_svc` writes.

## 5. The deriver — options_svc

- **Cursor:** `derive_cursor(session TEXT PRIMARY KEY, last_ts INTEGER)` in
  `gex_history.db`, written **in the same transaction** as that minute's rows. A
  crash between "rows written" and "cursor moved" cannot happen, so a minute is
  never skipped or doubled.
- **Loop** (replaces the `gex_due` fetch branch): each tick, read the raw store's
  `minutes` after the cursor, and derive them **oldest first**. Live = a backlog of
  one. Catch-up = a backlog of N. **One code path** — no separate replay mode to
  drift from the live one.
- **Not gated on the window.** The loop runs whenever a session's cursor is behind
  that session's last complete raw minute, so a deriver back at 15:30 finishes the
  15:10–15:20 backlog. On start it first finishes any EARLIER session still behind
  (oldest first, within `keep_sessions`), so an outage overnight from 15:00 is
  derived before the next session's first minute.
- **Per minute:** exactly today's work, fed from the raw chain instead of a fetch:
  the engine pass → gex/charm/dex/vanna/prem rows (+ `tprem`, `hiro_minutes`), the
  `on_chain` hooks, then `run_flow_alerts` (alerts dated at the MINUTE, not at the
  wall clock).
- **Stateful pieces** (HIRO, `flow_sides_tick`, `traded_premium`, the UOA /
  big-delta volume detectors, ETH eligibility) carry memory minute to minute. On
  process start the deriver first replays the session from its first raw minute up
  to the cursor with **state only** (no engine pass, no writes), then derives from
  the cursor on. State after a restart equals state without one.
- **Raw gaps:** a `gaps` minute produces no rows; HIRO and the tallies treat it as
  today's missed poll (re-seed; unlabelled volume).
- **Pushes:** every alert is recorded; a push is sent only when
  `now - minute <= [catchup] push_max_age_min`. A suppressed push is logged and
  counted, never silent.
- **Cache views** (`matrix`, `net_premium`, `flow_skew`, the gamma current view)
  are published after the last minute of a pass, not once per replayed minute.
- **Lag:** `/health` carries `derive_lag_min`; `up: false` beyond
  `[catchup] lag_alarm_min` (default 5) inside the window.
- **Speed target:** derive one minute in **under 2 s** on prod (a 4-hour backlog
  then clears in under 8 minutes). Measured during `tee` (§7) before `on` is allowed.

## 6. Units and promote

- **Two targets** generated by `deploy/systemd/generate_units.py`:
  - `trading-<env>-feed.target`: proxy + `feed_svc`.
  - `trading-<env>.target`: the seven services + `webgui` + `webgui_live`;
    `Wants=`/`After=` the feed target, so starting the app starts the feed, while
    stopping the app leaves it running. Services keep `Requires=` on the proxy —
    that dependency stops dependents when the PROXY stops, never the reverse.
- **The feed's file set:** `tools/feed_files.py` computes the import closure of the
  proxy and `feed_svc` plus the config files they read and `requirements.lock`.
  `tools/tests/test_feed_files.py` pins the set, so a new import cannot slip past it.
- **`promote.sh`** asks a Python helper (in `tools/promote_facts.py`) for
  `{in_window, feed_touched: [files]}` on `PREV..NEW`:

  | | no feed file touched | feed file or lock touched |
  |---|---|---|
  | **inside the window** (trading day, 06:30–15:20 CT) | stop/start the **app target only**; print the deriver's lag when back | **refuse before stopping**, list the files, suggest after 15:20; `--feed` overrides and accepts the gap |
  | **outside** | both targets, as today | both targets |

  All existing pre-stop checks run first; the rollback trap restores only what was
  stopped. A lock change always counts as a feed change: a running proxy must never
  have its packages reinstalled beneath it.

## 7. Cut-over — `config/feed.toml [feed] mode`

| mode | fetches | raw store | `gex_history.db` written by |
|---|---|---|---|
| `off` | options_svc (today) | — | options_svc's collector (today) |
| `tee` | options_svc (today) | written by options_svc's collector | the collector; a **shadow deriver** re-derives each minute from raw and compares field by field, logging every difference and the per-minute cost |
| `on` | `feed_svc` | written by `feed_svc` | the deriver |

`tee` proves derive-from-raw is identical and measures its cost **without one extra
Schwab call**. The shadow deriver runs on its own worker, writes nothing to
`gex_history.db` or Redis, sends no push, and skips ahead (logging how many minutes
it skipped) rather than ever delaying the live collector. `on` is enabled only after a clean `tee` session and the speed target
met. `off` from any mode returns to today with one restart.

## 8. Visibility

- **Status page — Feed card:** last raw minute, deriver lag, today's raw gaps with
  reasons, mode.
- `feed_svc` and the deriver both report through `/health`, the existing
  degrade counter and the scheduler-silence alarm.

## 9. Testing

- **Replay determinism (the key test):** a recorded sequence of raw minutes run
  through the deriver (a) straight through and (b) killed at minute k and
  restarted. Rows, alerts, cursor and stateful outputs identical.
- **Raw store:** the per-minute transaction, `minutes` as the completeness flag,
  `gaps` on restart, retention, read-only readers.
- **Push freshness:** an alert from an old minute is recorded and not pushed.
- **Promote:** `feed_files` pinned; the window/refusal table driven through the
  Python helper (no live systemd in tests).
- **`mode = "off"`** keeps every existing suite green.
- CI: a row for `services/feed_svc/tests` (`tests/test_ci_covers_every_suite.py`).

## 10. Configuration (all in `config/feed.toml`, catalogued in `webgui/config_schema.py`)

`[feed] mode` (`off`) · `keep_sessions` (5) · `stale_alarm_min` (2) ·
`[catchup] push_max_age_min` (3) · `lag_alarm_min` (5) · `ports.toml [services] feed = 8218`.

## 11. Documentation to update with the build

CLAUDE.md (unit order and ports, the eighth service, the promote rule, the
Tier-1/Tier-2 notes), `docs/reference/running-and-environments.md`,
`docs/dev-prod-environments.md` (runbook), the Technical Reference (collection and
catch-up), the API Reference (the raw store, `/health` fields), `webgui/page_help.py`
(Status), and the CHANGELOG.

## 12. Out of scope

- Recording streamer ticks (a later slice; the stream lives in the proxy).
- Surviving a proxy restart or a box reboot without a gap (impossible for chains).
- Moving the 15-minute autoscan's chain fetches onto the raw store (a possible
  later saving: it fetches its own chains today).
- Marking raw gaps on the Gamma heatmap.
