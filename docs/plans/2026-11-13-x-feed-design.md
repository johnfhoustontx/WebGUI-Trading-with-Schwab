# X Feed inside the app — design

Status: draft for approval, 2026-11-13. Branch `claude/x-feed-in-app`.

## Intent

The user follows 28 X accounts (in topic groups) through a standalone "X News
Feed": a zero-dependency Node 24 app on vps2 (`~/x-news-feed`, systemd user
unit `x-news-feed`, SQLite at `~/x-news-feed/data/feed.db`), reachable through
a separate Caddy route (`cos.neuralstrike.co/feed/`, basic auth) and a tailnet
serve on :4300. It works, but it lives outside the app, behind a different
login, on a different stack.

The user wants it **inside app.neuralstrike.co, behind the app's existing
password + TOTP sign-in, as a proper part of the app** — built and run by this
repo's rules (3 tiers, config, tests, docs, promote), not a separate site
bolted on. Then the standalone pieces are retired.

Source of behavioural truth for the port: the Node app's source and design
(on the user's PC at `D:\X-Posts`; `src/poller.js`, `src/mapStatus.js`,
`src/fxapi.js`, `src/db.js`, `src/server.js`, `public/app.js`, and
`docs/superpowers/specs/2026-11-10-x-news-feed-design.md`). Where this doc
says "as today", it means exactly what that code does.

## Goals

- An **X Feed** page at `/news/x`, reached from the MARKETS menu, behind the
  login, doing everything the standalone feed does:
  - topic group tabs (All · each group · Ungrouped, with post counts);
  - a watched-accounts panel: add (handle + optional group), remove (with
    confirm), change group, per-account "checked Ns ago" / error;
  - filters: search (text, handle, name; `$TICKER` matches cashtags), type
    (All / Posts / Reposts / Replies), single account chip, ticker;
  - the "most mentioned" tickers strip over the currently filtered posts;
  - live updates with the NEW highlight; gap markers; one-line rows that
    expand to full text, photos, video poster + link, quoted post, counts,
    "Posted …", "Open on X ↗";
  - scrolling back through the whole history (not just the latest window).
- Polling and catch-up behave **as today**: baseline on add, posts going
  forward only, catch-up after downtime with the 25-page cap and a visible
  gap, reposts never back-filled, the 15-minute live rule, per-account
  backoff, "not found" after repeated 404s, FxEmbed false-404 retries.
- One addition (user agreed): clicking a `$TICKER` chip in an expanded row
  offers the app's Symbol page for it, as well as filtering the feed.
- The existing data (28 active accounts + 1 inactive, 3 groups, ~4,150 posts
  incl. 564 hidden baseline rows, 1 gap) is carried over intact.
- Documented per this repo's rules; the standalone feed retired with the
  user's OK.

## Non-goals

- X posts on the public `live.` screens.
- Mixing X posts into Headlines (`/news`) or onto the Desk.
- Telegram / Discord / SMS alerts for posts.
- Any X API other than FxEmbed; any login to X.
- Fixing the hand-edited `cos.neuralstrike.co` Caddy block that
  `deploy/caddy/generate_caddyfile.py` does not know about (a separate
  follow-up; see Retirement).

## Anti-goals (failure even if it "works")

- Old posts flooding in as new — after migration, after a promote, or after
  re-adding an account. "Forward only" must survive the move.
- Losing or duplicating the migrated history.
- Breaking the existing Market News page or its bookmarks (`/news` stays).
- Third-party text rendered as HTML (XSS) — every X string goes through
  `ui.label` / `ui.link`, never `ui.html` (as `pages/news.py` does).
- A Node runtime, or any second language, added to prod.
- The X poller stalling or slowing any other service.

## Constraints (from the repo)

- Tier 1 (`webgui/`) imports only the allow-list: no `sqlite3`, no HTTP to
  FxEmbed, no engine imports. It reads Redis views via `bus_client`, watches
  versions, and enqueues commands with `bus_client.request()`; commands get
  no direct reply — answers come back through Redis.
- Tier 2 services are Python on `services/_scaffold.make_app` (health,
  heartbeat, scheduler supervision, `cmd:{domain}` consumer). Stores are
  stdlib `sqlite3`, WAL, under `services/<svc>/data/` (gitignored, so in the
  nightly backup).
- Tunables live in `config/<name>.toml` via `shared/config_toml`, each with a
  `webgui/config_schema.py` entry (STANDING RULE).
- Port 8218 and the name `feed_svc` are reserved for the planned options-chain
  feed. This service is **`xfeed_svc`, domain `xfeed`, port 8219**.
- THE DEVELOPMENT RULE: worktree → verify → merge `main` → push → prod only
  via `ssh vps2 'cd /home/administrator/dev && tools/promote.sh'`. There is no
  dev environment; promote stops and restarts the whole trading stack, so its
  timing is agreed with the user. Sudo steps (Caddy) are the user's.
- Pages repaint by polling `{key}:ver` (`view_watch.watch_view`, 2 s), not by
  push.

## Approaches considered

1. **New Python Tier-2 service `xfeed_svc` + a webgui page. — CHOSEN.**
   Port the poller and its tests to Python; the service owns the SQLite store
   and publishes to Redis; the page reads Redis and sends commands. The only
   option that is "a proper part of the app" by the repo's rules (health,
   Status page, backups, CI, config, promote all apply). Cost: the most
   porting, and filtered/historical reads become a round trip through Redis
   (a fraction of a second) instead of an instant local query.
2. **Fold X into `news_svc` as another source.** Fewer registration points,
   but couples X's catch-up/backoff state machine to news cadence, restarts
   and failures. Rejected.
3. **Keep the Node app as the Tier-2 process, publishing to Redis.** Least
   rewriting, keeps the 79 Node tests, but puts Node into prod and fits none
   of the service conventions; every future change happens outside the app's
   normal workflow. Rejected.

Within 1, the read path is **hybrid** (user agreed): the newest window is
pushed for the live view; anything filtered or older is answered on request.
Pushing a week of posts on every change would make multi-MB Redis payloads and
still not reach older history.

## Design

### Navigation (user decided)

Market News becomes a **group with a tab strip**, like Strategy Tools:

```
MARKETS
  Dealer Positioning
  Opportunity Board
  Flow Alerts
  Market News ▸   [ Headlines | X Feed ]
  Trend & Sentiment
```

- `/news` keeps its route and content; its tab label is **Headlines**.
- `/news/x` is the new **X Feed** page.
- The rail entry stays labelled "Market News" and still lands on `/news`.

### Tier 2 — `services/xfeed_svc`

Modules (names are the builder's; avoid stdlib names):

- `app.py` — `make_app("xfeed", scheduler=…, command_handler=…)`, uvicorn on
  `127.0.0.1:SERVICE_PORTS["xfeed"]`.
- `fxapi.py` — FxEmbed client for
  `https://api.fxtwitter.com/2/profile/{handle}/statuses?with_replies=1&cursor=`,
  ported from `src/fxapi.js` **including its false-404 retries** and the 15 s
  timeout. Uses `requests` with a total-deadline guard like
  `news_svc/fetch.py`.
- `mapping.py` — `mapStatus` / `extractCashtags` / media / quote mapping, as
  today.
- `poller.py` — `take_baseline`, `poll_account`, `assign_sort_times`,
  `backoff`, the cycle runner (accounts one after another, ~1 s gaps, pages
  ~1 s apart), as today. The in-memory "polled since start" set lives in the
  service process, so the first cycle after every service start is a
  catch-up (non-live) cycle — exactly as today after a Node restart.
- `store.py` — the SQLite store. **Same schema as today** (`accounts`,
  `posts`, `gaps`, `settings`; times are epoch **milliseconds**; JSON columns
  as today), so migration is a copy. `check_same_thread=False`, busy timeout,
  `BEGIN IMMEDIATE` writes, WAL, like `news_svc/store.py`. `_migrate` on open
  as the repo does.
- `handlers.py` — commands and publishing (below).
- `scheduler.py` — the loop: heartbeat, re-read config, run a poll cycle when
  due (interval measured from the end of one cycle to the start of the next,
  as today). Adding an account must not wait behind a long catch-up cycle,
  and a cycle must not run the same account concurrently with an add.

### Tier 3 — Redis views and commands

Views the service publishes (`bus.cache_set`, `skip_unchanged=True`, no `ts`
in payloads):

| View | Contents | When |
|---|---|---|
| `cache:xfeed:latest` (+ `events:xfeed:latest`) | newest 300 visible rows across all active accounts, merged with gap rows, plus top-10 tickers | after any cycle/add/remove/regroup that changed it |
| `cache:xfeed:accounts` (+ event) | active accounts (handle, name, avatar, group, last checked/ok, error, fail count), per-account and per-group visible post counts, the poll interval | after every account poll and every account change |

Commands on `cmd:xfeed`, each carrying a `request_id`:

| Command | Args | Answer |
|---|---|---|
| `add_account` | `handle`, `group?` | ok + account, or "Invalid handle" / "Already watching @x" / "Account not found" / "Could not reach FxEmbed (…)" / group too long |
| `remove_account` | `handle` | ok, or "Not watching that account" |
| `set_group` | `handle`, `group` (null = Ungrouped) | ok, or error |
| `query` | `group?`/`ungrouped?`, `account?`, `type?`, `q?`, `ticker?`, `before?`, `before_key?`, `limit` (≤ 1000) | rows (posts + gaps, as `queryFeed` today), counts, top tickers |

Answers go to a **per-request key** `cache:xfeed:answer:{request_id}`, written
with a short TTL (config; ~120 s) and no event. While a request is pending the
page polls that one key quickly (config; ~0.25 s) until it appears or the
kit's busy timeout passes. *(Claude's call — per-request keys rather than the
single `cache:blog:result` view, because two open tabs querying at once would
otherwise overwrite each other's 300-row answers.)* Validation (handle
normalisation, group cleanup: trim, collapse spaces, ≤ 30 chars, reuse an
existing group's spelling case-insensitively) happens in the service, as
today's server does.

### Tier 1 — `webgui/pages/xfeed.py` (+ a pure `xfeed_view.py`)

- A leaf module with `render()`, registered with `@_page("/news/x")`, in the
  Market News tab group, with `_TAB_COLOR`, built from `pages/ui_kit.py`,
  Tailwind only, theme tokens, `fmt`/`copy`, handlers in `ui_guard`. Pure
  formatting/filter helpers go in a separate view module so they are unit
  tested without NiceGUI (the `news.py` / `news_view.py` split).
- **Data flow:**
  - No filter active and no older pages loaded → render `cache:xfeed:latest`;
    on its version change, repaint. A row is NEW **as today**: origin `live`
    and first seen by the service less than ~60 s ago (config) — so a tab
    opened 30 s after a post arrived still shows it NEW. *(Claude's call.)*
  - Any filter/group/account/ticker active, or "load older" → send `query`
    and render the answer. When `latest`'s version changes while filtered,
    re-run the current query.
  - Load older: a "Load older posts" control at the bottom (and/or on
    scroll), sending `query` with `before`/`before_key` from the last row,
    exactly as today's `loadMore`.
  - `cache:xfeed:accounts` drives the accounts panel, group tabs and chips.
- **Look:** the app's style, not a pixel copy of the old page. One-line rows
  like `pages/news.py draw_rows`: Central-time stamp with date dividers, type
  word (POST / RT / REPLY), first line of text, media indicator, up to 2
  ticker chips then "+N", handle on the right; NEW rows highlighted. Accounts
  panel to the right on wide screens, below on narrow ones.
- **Expanded row:** full text (links, @mentions, `$TICKERS` as links —
  mentions/links open on X; a cashtag filters the feed and offers "Open
  Symbol page" → the app's Symbol route), photos inline (open full size),
  video poster + link, quoted post inset, counts, "Posted …", "Open on X ↗".
  Every URL from X passes the same `safe_href` check `news_view` uses.
- **Status line:** "Live · checking every 60s" from the accounts view, amber
  or red when the service is stale/down (heartbeat / the Status page's
  health), and "Updated HH:MM".
- Commands are sent only when `shell.may_enqueue()` allows, as `news.py`
  does.

### Poll interval *(Claude's call)*

The interval moves from the feed's DB `settings` table to
`config/xfeed.toml` (default 60 s, minimum 30 s), editable in **Settings →
Configuration**, re-read by the scheduler each tick so no restart is needed.
The page shows the interval and links to that setting instead of having its
own interval buttons — one source of truth, using the app's existing config
editor rather than a second writer. (The migrated `settings.intervalMs` is
60000, the default, so nothing is lost.)

### Configuration — `config/xfeed.toml`

Every tunable, each with a `config_schema.py` entry: poll interval and
minimum; account gap and page gap; max catch-up pages (25); request timeout
(15 s); FxEmbed retry count/delay; backoff steps (60 s / 2 m / 5 m);
not-found limit (3); live-rule slack (15 min); "recently checked" factor
(3 intervals); `latest` window size (300); query limit cap (1000); top
tickers count (10); answer TTL; page answer-poll interval; NEW highlight
seconds; group name max length (30). The FxEmbed base URL and User-Agent too.

### Registration (everything a new service touches)

`config/ports.toml [services] xfeed = 8219`; `repo_paths.py` data constants;
`.gitignore services/xfeed_svc/data/`; root `conftest.py _LIVE_DIRS`; Status
page `svc_labels` (+ its tests); `services/tests/test_scaffold.py`
parametrize; CI suite row (+ `test_ci_covers_every_suite.py`);
`config_schema.py` service label + keys; `test_shell.py` route list;
`test_no_inline_style.py`. Systemd units are generated from
`SERVICE_PORTS` by promote — nothing to hand-write.

### Data migration

A one-shot tool, `tools/import_x_feed.py`, run on vps2 **after** the promote
that ships it (prod is the only checkout and only promote moves it, so the
tool does not exist there earlier), with both the old feed and `xfeed_svc`
stopped:

1. Take a consistent copy of `~/x-news-feed/data/feed.db` (SQLite backup
   API, so the WAL is included).
2. Refuse if the target `services/xfeed_svc/data/xfeed.db` already has
   accounts (no silent overwrite; `--force` to replace).
3. Copy all rows (including inactive accounts, hidden baseline posts and the
   gap) into the new store, then verify counts per table match and print
   them.

Because the schema is unchanged and the poller's `last_ok_at` per account is
carried over, the first cycle after the new service starts is a normal
catch-up from where the old feed stopped: posts made during the switchover
appear as caught up at their real times, never as a flood, and anything
beyond 25 pages becomes a visible gap.

### Cut-over order (timing agreed with the user) *(Claude's call: promote first, then import)*

1. Merge to `main`, push.
2. `ssh vps2 'cd /home/administrator/dev && tools/promote.sh'` — generates and
   starts `xfeed_svc` with the rest of the stack. Its store is created empty,
   it has no accounts, so it polls nothing and publishes empty views; the X
   Feed page shows an empty state ("No accounts yet") for these few minutes.
   **The old feed keeps running throughout**, so if promote rolls back there
   is nothing to undo.
3. On vps2, once promote has succeeded: stop the new unit
   (`systemctl --user stop` on `xfeed_svc`'s generated unit), then
   `systemctl --user stop x-news-feed` and `disable` it.
4. Run `tools/import_x_feed.py` (empty target, so no `--force`).
5. Start `xfeed_svc`'s unit again. Its first cycle catches up from each
   account's carried-over `last_ok_at` (the old feed stopped only moments
   earlier).
6. Verify on app.neuralstrike.co (below). If the new page is wrong, the old
   feed can be re-enabled and started; its DB was only read.

So the service must handle an empty store (no accounts) cleanly, and the
import tool must be runnable from the prod checkout's venv.

### Retirement (each step with the user's OK)

- `x-news-feed` user unit: stopped and disabled at cut-over; after a few
  days of the new page working, remove the unit file and archive
  `~/x-news-feed` (keep `feed.db` as a backup).
- `cos.neuralstrike.co/feed/`: the user removes the `handle_path /feed/*`
  block from `/etc/caddy/Caddyfile` (sudo), validates and reloads Caddy. We
  supply the exact edit after diffing against
  `/etc/caddy/Caddyfile.pre-feed`.
- Tailnet serve on :4300: turned off (`tailscale serve --https=4300 off`;
  sudo if the operator isn't set).
- Follow-up, not this work: teach `generate_caddyfile.py` the hand-edited
  `cos.neuralstrike.co` block (cos-hub), so a regenerated Caddyfile no longer
  drops it.

## Testing and verification

- **Port the Node tests to pytest** (79 today: poller, mapping, FxEmbed client,
  store, server/API behaviour) against the Python service, with FxEmbed
  faked; network and live DBs are blocked under pytest as usual.
- Tier-1 tests for the pure view module (row text, time/date dividers,
  cashtag/link splitting, safe hrefs, NEW marking) and the shell/route/style
  guards.
- A migration test: a fixture DB in today's schema → import → identical rows.
- Before merge: the full suites the repo runs, plus `tools/ui_harness.py` on
  `/news` and `/news/x` against fakeredis data.
- After promote, on prod: `xfeed_svc` healthy on the Status page; `/news/x`
  shows the migrated history and groups; adding and removing a test account
  works; a new post appears live; filters and load-older work; `/news`
  unchanged.

## Docs

Per the repo's "where a fact belongs" table: this design and its plan in
`docs/plans/`; `docs/CHANGELOG.md`; `docs/webgui-routes.md` (`/news/x`, and
`/news` becoming a tab group); the manuals that cover pages, services, ports
and commands, plus `webgui/page_help.py`; `CLAUDE.md` only where an invariant
changes (the service/port list); the manuals' diagram generator for the new
service.

## Left to the builder

Module and function names; how the scheduler and command consumer avoid
running one account concurrently; exact view payload field names; the row
and panel layout within the app's style; which manual sections need edits;
the order of work in the plan.
