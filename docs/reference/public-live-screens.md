# The public live screens

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

## The public live screens — a SECOND Tier-1 process

`webgui/live_main.py` serves **eighteen screens, unauthenticated, to
anyone** on `nicegui_live` (prod :8501, dev :9501) behind `LIVE_HOST`
(`live.neuralstrike.co`). It renders the **real page modules the app renders**, so a
published screen cannot drift from the private one. The published set and every pin
are pure data in **`webgui/live_screens.py`** (`SCREENS` · `SETTINGS_PINS` ·
`PUBLIC_PINS`), read by the route registration, `tools/capture_live_shots.py` and the
static grid on `neuralstrike.co/live.html` alike — so **adding a `Screen` publishes a
route**. `Screen.tile = False` (Option Signals, the four interactive tools and Market News) publishes the route but
draws no grid tile and takes no capture; the site's Tools menu reaches them. `Screen.parent`
(the four extra $SPX Gamma views) draws a small link under that parent's tile instead. Per-screen detail: [docs/webgui-routes.md](../webgui-routes.md); design +
plan: [`docs/plans/2026-09-07-public-live-screens-{design,plan}.md`](../plans/2026-09-07-public-live-screens-design.md).

⚠ **NO page may `import main`, and neither may `live_main`.** `main.py`'s module body
registers every `@_page` route, so importing it from a second process publishes
`/terminate` and `/settings` to the internet — silently, while looking entirely
correct. The seam a page needs is **`webgui/shell.py`** (`subtab_slot` ·
`set_breadcrumb_leaf` · `bind_breadcrumb_leaf` · `play_alert`, plus the page-level
`TABLE_CSS` / `SUBTAB_CSS` / `PANEL_SCROLL_CSS` that **both** entrypoints inject — those style widgets a
PAGE mounts, not nav chrome — plus, since 2026-09-09, the **brand lockup**
(`brand_mark_src` / `brand_lockup_html` / `_STATIC_DIR`), which both headers
draw). `main` re-exports every one of them, so nothing else
moved. ⚠ The lockup is what made `shell.py` stop being import-free: it reads
`[brand]` out of `pages.options.theme`, escapes with `html`, resolves the mark
with `pathlib` and asks `repo_paths` for the DEV chip. That import list is
CLOSED and pinned — a page module, `bus_client` or `app_settings` appearing
there means the seam is becoming main.py again. `test_shell_seam.py` pins the absence at source level; `test_live_main.py`
pins it again by running `live_main.py` ALONE in a fresh interpreter and asserting
`main` never entered `sys.modules` — the only check that can see a TRANSITIVE import,
which is how one would actually arrive.

**Read-only is FOUR layers, and the three this process installs go in BEFORE any page
module is imported** — hence `live_main.py`'s `# noqa: E402` import order, which is
load-bearing rather than untidy: (1) a Redis **ACL user** from `REDIS_LIVE_URL`, the
structural one, enforced by the server rather than by this process — ⚠ and the only
layer that can be **ABSENT while everything looks correct**, since unset it falls back
to the stack's ordinary full read/write credential, so `live_main.resolve_acl_url`
warns and `require_acl_url` **refuses to serve prod** without it, or with a URL that
names the default user (dev warns: dev's live origin is not fronted by the edge).
`require_read_only` then PROVES it at start: a `SET` on a cache key and an `XADD`
on a command stream must both be refused, or prod does not serve; (2)
**`bus_client.set_read_only(True)`** — `bus_client.request` is the **single Tier-1
write chokepoint**, so one refusal covers every command on every page, and on these
pages that reaches `gamma_analyze` / `gamma_explain` (**paid Claude calls**) and
`gamma_refresh` / sentiment `refresh` (Schwab fetches against a budget already at
about 84k/day, measured 2026-10-02); (3) **`app_settings.freeze(pins)`** — `set()` becomes a no-op and
`load()` never touches disk; (4) structurally, no rail, Settings, Terminate or
Sign-out, because those routes **do not exist in the process**. The pinned gamma
screens refuse at the page as well: `gamma.may_enqueue(symbol, view)` gates every
enqueue site (a *total* proof, pinned by an AST walk over the source) **and** no
control that reaches one is built. Both, not either.

⚠ **Since 2026-09-21 the origin has exactly THREE write paths, and none is a
hole in layer 2.** `test_bus_client.py` pins the set of writing functions to
`request` plus the five public writers below. The first:
`bus_client.request_public_scan(symbol)` puts `{"symbol": <SYMBOL>}`
on `cmd:finder_public` for the public Strategy Finder; `request()` stays refused
for every domain, that stream's included. The function takes one argument, runs
it through `clean_symbol`, and chooses neither the stream nor the command type
(an AST test pins all three). Layer 1 agrees: the `live` ACL user writes only
through Redis 7 selectors, one XADD-only selector per public stream - this one
`(%W~cmd:finder_public +xadd)` (runbook §2 step 4c).
options_svc answers on a consumer loop of its own
(`make_app(extra_consumers=...)`, `services/options_svc/finder_public.py`), so a
visitor's scan never queues with the owner's commands, and every refusal —
invalid, expired, cached, duplicate, closed, over budget — is decided before
any Schwab call. Result keys are per symbol and EXPIRE, because visitors choose
the symbols. The page is `/finder` (`swing.render(public=True)` hands off to
`pages/options/finder_live.py` before the private page builds anything); it
counts each visitor's requests in memory (`webgui/visitor_limit.py`, a copy of
`main._client_ip`'s edge rule — the public process cannot import `main`), reads
only its own symbol's entry of the status view, and draws no owner control.
Roadmap:
[`docs/plans/2026-09-21-public-strategy-finder-roadmap.md`](../plans/2026-09-21-public-strategy-finder-roadmap.md).

**The second path is the public Rescue form's** (`/rescue`):
`bus_client.request_public_ladder(symbol, expiry=None)` and
`request_public_rescue(spec)` on `cmd:rescue_public`, answered by
`services/options_svc/rescue_public.py` on a third consumer loop, behind a
second ACL selector (runbook §2 step 4d). ⚠ **The visitor controls a whole
trade here, not one string**, so `shared.public_rescue.clean_spec` keeps only
the fields `compute_rescue_adhoc` reads and refuses the trade outright on any
unusable one (a NaN, an infinity, a bool, a past expiration) - on the page
before sending, and again in the worker. Three differences from the Finder,
each deliberate: the strikes list carries **no quotes** (bid/ask/mark are
dropped before the write); results are keyed by a **hash of the normalized
trade**, so identical trades share one compute and no key NAME spells a trade;
and **no list of requests is kept** - the status view holds counts only, and
every request gets its own answer key the page polls, so there is no
Finder-style `last` map. ⚠ That is not "nothing is stored": each result holds
its trade for `result_keep_min` (30 min), readable by any Redis read
credential including the public process's, and the hash is unsalted. A
failed compute is answered `error` and never cached (the engine returns
`{"error": "<ExcType>: ..."}` rather than raising, and that text is not for a
stranger's screen); a failed strikes fetch is remembered nowhere; each strike
must be on the listed ladder; and one trade STRUCTURE (the trade less its
prices) runs at most `structure_runs` times per reuse window, since every
typed price is otherwise a fresh compute against the shared budget. `rescue.render(public=True)`
hands off to `pages/options/rescue_live.py` before the owner's at-risk board is
built, and every private enqueue in `rescue.py` opens with `_may_enqueue`, as
`test_live_commands.py` requires of any published module. Blueprint:
[`docs/plans/2026-09-21-public-rescue-adhoc-roadmap.md`](../plans/2026-09-21-public-rescue-adhoc-roadmap.md).

**The third path is the public Calculator and Simulator's** (`/calculator`,
`/simulator`): `bus_client.request_public_tool` on `cmd:tools_public` (what
spends Schwab calls: a chain, one more expiration, a rating, a Simulator
snapshot) and `request_public_math` on `cmd:tools_public_math` (pricing over
data already held: reprice, implied volatility, a what-if sweep), each read by
its own loop in `services/options_svc/tools_public.py` behind its own ACL
selector (runbook §2 steps 4e, 4f). ⚠ **Two streams because a snapshot fetch
must never stall the reprice every edit triggers.** ⚠ **Redis is readable by
the public process, so a quote written there is a quote published**:
`public_chain` HOLDS the quoted chain in the worker's memory and writes
`cache:options:pub_chain:<SYMBOL>` stripped to expirations and strikes, with a
four-field quote block only while `public_scan.show_leg_quotes` is on. ⚠
Turning the switch off stops the pages drawing quotes at once (`calc_live.page_chain`
strips the block from what the page holds), but keys written while it was on keep
their quotes block until the next request for that symbol rewrites them or they
expire (`ladder_keep_min`): the public process can read them, no page draws them.
That one key per symbol is shared with Rescue's strikes list, so the three tools share
one fetch. ⚠ **A rated row's legs are rebuilt from an ALLOW-list**
(`tools_public.LEG_KEYS`), never a deny-list, and while quotes are off a rating
with an unpriced option leg is refused `price_needed` - otherwise the engine
priced it at the chain's mark and the row revealed that quote (a review caught
exactly that). ⚠ **Derived values can still be worked back toward quotes** - the
implied-volatility percentage, the sweep's model prices, some scores - an open
owner decision beside D2. **One daily budget** covers Rescue, the Calculator and
the Simulator (`public_budget`, the limit in `rescue_public.toml [budget]`); a
`threading.Lock` suffices only because every public worker is a thread in the
one options_svc process. ⚠ **The two tools windows (`[windows.rescue_public]`,
`[windows.tools_public]`) mean "prices are live", not "allowed"**: with
`after_hours = true` the workers gate on `market_calendar.open_for`, run at any
hour and the pages warn that bid, ask and mark may be stale
(`pages/options/after_hours.py`); a chain loaded before the open is reloaded
once the window opens (`market_calendar.opened_since`). The public Simulator's snapshots live in their own
`compute.SimStore` (`tools_public.PUBLIC_SIM`), never the owner's
`_SIM_SNAPSHOTS`, and an extension is copy-on-write. The Calculator hands its
position to the Simulator through NiceGUI **tab storage**
(`pages/options/public_handoff.py`): the Calculator only writes, the Simulator
only reads, nothing outlives `handoff_keep_min`, and the private pages'
`shared_position` / `page_state` / `app_settings` are refused because each is
ONE store every visitor would share. Design:
[`docs/plans/2026-09-21-public-calculator-simulator-design.md`](../plans/2026-09-21-public-calculator-simulator-design.md).

**Market News (`/news`, 2026-09-26) is the one Tools screen that writes
nothing.** `news.render(public=True)` hands off to `pages/news_live.py`, which
has no command site and reads three public views alone — `cache:news:feed_public`
and `cache:news:sec_public`, which `news_svc` builds from the rows whose PRIMARY
feed is public under the CURRENT `[feed_flags]` (re-checked at every publish,
tickers cut to those a public feed contributed, impact RE-SCORED from that public
row against the collection list, never `[tickers] extras`), and
`cache:news:calendar_public`, BUILT with the collection list so an extra's
dividends never reach it. ⚠ **The ACL is not a layer here**: the `live` user's
`~cache:*` read covers the private `feed`, `sec`, `calendar` and
`calendar_status` (which carries error text) too, and a wildcard cannot exclude
one key beneath it, so the code is the only guard (`test_news_live.py`). That binds
the **Desk** as well, a published screen: its headlines strip reads through
`desk.bus_key(view)`, which swaps `news:feed` for `news:feed_public` when
`shell.is_public()`. Any new public reader of the news feed needs the same swap.

**Option Signals (`/signals`, 2026-10-07) is the Market Scanner, and it writes
nothing.** It is `scanner.render()` itself, gated on the PROCESS's origin
(`shell.is_public()` / `shell.may_enqueue()`) rather than on a `public=True`
argument: a keyword can be left off a `Screen` entry, and the result would be
the owner's page served to anyone. What it leaves out, and where each is
enforced:

- (The page itself changed on 2026-10-07, on both origins: each of the 0-DTE and
  Swing tabs holds ONE table of credit spreads and other structures together,
  where it held two behind a switch. Nothing below depends on which.)
- **Run scan** is not built, and `_request_scan` opens with the `_may_enqueue`
  return `test_live_commands.py` requires of every published module.
- **Paper trade, Calculator and Expected Move** are not built. Paper writes the
  owner's book; the other two hand off through `handoff._pending`, ONE store
  every visitor would share.
- **`handoff.watch_paper_results` is not started.** `cache:options:paper_create`
  is the answer to the owner's own Paper click, and would be toasted to every
  visitor.
- **The ledger caps are never read.** `checks_feed.read_context` refuses
  `options:ledger_caps` on the public origin whatever the caller passes. It is
  refused THERE because the Trade detail panel reads a context of its own when
  its page holds none. `_build_populate(public=True)` also closes every row's
  `_allow_paper`, so `checks._book` draws no line at all: absent rather than
  grey, which is what lets a row still read Clear.
- **No "new" badge.** `scanner._SEEN` is one set for the whole process: read
  there, one visitor's page load would decide every other visitor's badges.
- **No Max contracts row and no dollar figure for the per-trade cap** in "Why
  no trade?" (`funnel_cards(public=True)` reads the entry without
  `max_risk_dollars`). Both are the owner's risk limit.
- **The quotes switch applies, to what it names.** While
  `public_scan.show_leg_quotes` is off (`checks_feed.quotes_withheld`), the
  panel is built without the per-contract Greeks (`detail._CONTRACT_GREEKS`)
  and draws no Greeks section, and the checklist is judged without
  `friction_pct`, so the cost-to-trade line reads as not measured. Theta and
  vega are never printed on this origin, switch on or off
  (`detail._NEVER_PUBLIC`, the Calculator's `tools_public.ROW_NEVER`).
  `detail._build_cards` is also the public Calculator's rating panel, so both
  rules reach that screen.
- **A single option's price follows the switch too** (the owner's decision,
  2026-10-07). A one-leg row's Debit, Max loss and breakeven ARE that option's
  price, and its R:R is a function of it. While quotes are withheld,
  `_build_populate(withhold_quotes=True)` swaps every single-option candidate
  for `strategy_table.public_signal(...)` BEFORE anything is built from it: a
  copy with no row-level price field (`SINGLE_OPTION_PRICE_FIELDS`) and legs
  rebuilt from an allow-list (`PUBLIC_LEG_KEYS`). The table cells read a dash,
  the panel says *The price of a single option is not shown on this page*, and
  the checklist has nothing to print. Which side is unbounded is written as
  explicit flags first, so a naked short still reads as undefined risk.
  ⚠ **Still printed, by decision:** a credit spread's PoP (one minus its short
  delta) and a multi-leg row's net debit or credit, both as the public Finder
  prints them. ⚠ The public Finder itself still prints a single option's price
  on request; that page is unchanged and remains part of the open decision D2.

⚠ **Every visitor draws from ONE build.** The day union reaches about 4.5 MB by
the close, and the private page gives each tab its own parse and its own five
thousand row dicts. On this origin `_read_and_build_shared` reads each scan
view through a version-gated copy of its own (`scanner._shared_view`) and
takes the built rows from `scanner_shared.get`: one slot for the process, keyed on the IDENTITY of the
day union, the live scan, the regime and the calibration (each the same object
until its view is republished), plus a `stamp` compared by value (today's
date, which the build gates the day union on and nothing republishes at
midnight; and whether quote figures are withheld, which is baked into each
row's chip), and rebuilt when it is HALF of `checks_feed.TABLE_REFRESH_SEC`
old, which is what re-stamps against the Opportunity Board. ⚠ Half, not the
whole: a build is stamped when it finishes, so with the limit equal to a tab's
own tick the tab's next tick finds its build a few milliseconds under it and
waits a second period (a review measured exactly that: ten minutes, not five).
`scanner._shared_view` returns the object it already holds when `:ver` has
moved ahead of the envelope (`Bus.cache_set` moves the counter first), or
every visitor polling in that gap would build the old scan again.
⚠ **What it returns is read-only, payloads and rows alike.**
The page stamps nothing onto it: the selected row's accent goes on copies of
the one page a visitor is sent (`scanner.page_rows`), and a re-stamp there is
a fresh shared build rather than a per-tab copy of every row. A stamp on a
shared row would show one visitor's click in every other visitor's tab.
⚠ **Not `bus_client.read_shared` for the two scan views.** That keeps the 48
views read most recently, and the public Gamma page alone can read that many;
a day union dropped between two visitors' reads comes back as a different
object, and the slot would then build once per visitor
(`test_the_shared_build_survives_a_busy_gamma_page`). The
per-tab cost this avoids is an estimate (about 20 MB by the close), not a
measurement.

**Flow alerts can carry service-stamped `quiet` / `public` flags** (since 2026-10-01,
the HIRO-model `hiro_surge` / `hiro_flip`). `quiet: True` = no push and no Desk speech;
`public: False` = hidden wherever `shell.hides_non_public()` is true — the public
origin **and** gallery captures (`ns_capture=1`), which publish to the website. The
one filter is `pages/options/flow._shown`, reached through `flow.alert_rows`; ⚠ **any
NEW reader that shows flow-alert rows or COUNTS must go through it**, and must call it
in the page context (it reads the request cookie, so inside `run.io_bound` a capture
would silently stop hiding). Tier 1 decides from the flag on the alert, never from
`[hiro]` config, and a missing flag means shown/spoken. As with the news feed, the ACL
is no layer here: the `live` user can read the whole `cache:options:flow_alerts`.

⚠ **The published route set is the eighteen screens, the eight 308 redirects in
`live_screens.RETIRED_ROUTES` (the pinned Gamma screens retired 2026-09-22, each
now a redirect to `/gamma`), PLUS exactly one non-page route: `/static`
(2026-09-09).** Every screen now carries a slim header — the
brand lockup, a hairline, the screen name, and **no navigation of any kind** —
and `[brand].mark` is a file under `/static`, which this process serves from its
OWN ASGI app: measured before the mount, `:8500/static/img/neuralstrike-mark.svg`
was 200 and `:8501` was 404, so the header would have drawn a broken image while
the markup read as correct. ⚠ **Mounting a directory publishes every file in it,
now and later** — that tree is three alert WAVs and four brand images, and
`test_live_main.py` pins both that `/static` is the only addition and that
nothing but bundled assets lives under it. `/voice` is deliberately NOT mounted.

⚠ **`app_settings.freeze()` is not only about pinning defaults.** `settings.json` is a
**single-user store whose in-memory cache assumes one writer in one process**;
unfrozen, the public process would read your live preferences — changing your own
Macro Board skin would re-skin the public site — and race you for the file.
`PUBLIC_PINS` holds the pins that belong to the ORIGIN rather than to any one screen:
today `voice_enabled: False`, because it **defaults True** and the Desk's spoken
alerts are `edge_tts` **network calls**, one per flow alert per visitor plus ~32 on a
first build (`desk._prewarm_clips` has no market-hours gate).

⚠ **The Redis ACL needs `@pubsub` and `@connection`, not just `@read`.** `SUBSCRIBE`
belongs to `@pubsub`; `SELECT` (any non-zero `redis_db`) and `PING` to `@connection`.
`EventListener._run` swallows a failed subscribe, so an under-granted ACL renders one
frame and then never repaints — **it reads as a frozen tape, not as a permissions
error**. Never grant `+publish` (a public process that can publish can spoof repaint
events to the private app), `@write`, or `@stream` — the streams are `cmd:*`.

⚠ **The live unit loads `.env.live`, NOT the stack's `.env`** — the one exception in
`generate_units._env_file`, and it is about BLAST RADIUS, not ownership. `.env` carries
`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `PROXY_SHARED_SECRET`,
`SMS_SMTP_APP_PASSWORD`, `DISCORD_WEBHOOK_URL` and `GAMMA_BRIEFING_WEBHOOK_URL`; the
public process needs `REDIS_LIVE_URL` alone (its own user and password are in the
URL; `shared.bus.client.env_password` hands the stack password to no URL that
carries a credential) and reads none of the others. Neither `.gitignore`'s `.env` line nor `backup_local.EXTRA_FILES`' entry
matches the new name, so both carry it explicitly. ⚠ **`REDIS_LIVE_URL` carries the
Redis DB INDEX in its path**, bypassing `repo_paths.REDIS_DB` — prod's line copied
into dev aims dev's public process at **prod db 0**. Setup:
[the runbook](../dev-prod-environments.md) §2 step 4b.

⚠ **`cache:options:gamma` is a single SYMBOL-AGNOSTIC slot**, and
`refresh_gamma_current` reads the symbol back *out* of it, so it is sticky and driven
by whatever the private app last looked at. The published screens therefore read
**`cache:options:gamma_pub:<SYMBOL>`** (+ `gamma_pub_hist_<SYMBOL>_<view>`), written
additively for `handlers.PUBLISHED_GAMMA_SYMBOLS` off chains the collector already
pays for. ⚠ Only the views listed in `PUBLISHED_GAMMA_HISTORY_VIEWS` get a history
key, each a ~1 MB grid rewritten every minute, so a newly pinned heatmap view is a
write-bandwidth cost rather than a free screen. Un-pinning a public screen's view
without adding it there draws an **empty
heatmap, silently**, because a missing history key reads as "no history yet".

⚠ **`deploy/site/live/*.webp` is generated, gitignored state under `SITE_ROOT`** — the
same shape as `webgui/data/`. Committed, the captures would dirty prod's tree the
moment the capture timer first fires, and **`tools/promote.sh` refuses a dirty tree.**
**`deploy/site/ideas/` + `deploy/site/ideas.json` are the same** — every POSTED hourly
trade idea's card, written by `options_svc/site_ideas.py` after the sends (never
raising, so a site write cannot cost a post) and drawn by `assets/ideas.js` on the home
strip and `ideas.html`. ⚠ Each card's RESULT follows the app's own exit rules
(`trade_mgmt.structure_rules`, operator overrides included): the option is MODELLED
(Black-Scholes at the entry price's implied volatility) on every 1-minute STOCK bar,
stop checked before target, a gap through a stop filled at the open. Never an option
quote: a live option mark on a public page is a published quote (the open D2
question). The scan is incremental (`checked_to`), so bars that arrive late are
still scanned and a hit keeps its true minute.
⚠ The options_svc conftest redirects `site_ideas.SITE_ROOT`;
a test that posts without it writes cards into this checkout.
**`deploy/site/reports/` is the same** — the market reports `report.html` frames, uploaded
from the operator's workstation by tooling that lives outside this repo
(`D:\NeuralStrike Reports\tools\publish.py`, which refuses to upload until prod ignores
the directory). ⚠ The frame's `reports/latest.html` name is the contract with that tool.
⚠ **So is its MARKUP, since 2026-09-16**: `market_svc/report_summary.py` reads that
file to publish `cache:market:summary` — which report the Desk's Market report dialog frames (the file's NAME is that dialog's `frame_url` too, since 2026-10-06) and the
WHOLE bottom ticker (since 2026-09-21 it shows nothing else) — from `div.slotchip`, the `h1` and each section `h2`. A renderer change
that renames those publishes nothing (the last good summary stays, with one WARNING),
so the Desk and the ticker go quietly stale rather than wrong. No Claude call sits behind the
summary any more.
**`deploy/site/assets/shots/*.webp` — the marketing gallery — is the same, with THREE
TRACKED EXCEPTIONS** (`image16/17/18`). Those name a Simulator view that lives in page
state rather than in the URL, so **nothing regenerates them**: ignored, a fresh clone
would have no picture for those tiles *ever*, not merely until the next capture run.
The pattern therefore excludes the **files**, not the directory — git cannot un-exclude
a file inside an excluded directory — and `.gitignore` does not apply to a path already
in the index, so such a pattern buys nothing until `git rm --cached` runs.

⚠ **The gallery capture AUTHENTICATES AS THE OWNER; its sibling cannot.**
`tools/capture_live_shots.py` reads the public origin and has no app access at all.
`tools/capture_gallery_shots.py` reads the 0600 `auth_store` file and **mints a session
cookie** — possible because `auth.mint_token` is stateless (no server-side registry of
issued tokens, by design), so a valid cookie is a pure computation over the store's
`session_secret` and `epoch`. What it mints is an ordinary full session, not a read-only
one, so this tool carries the private app's blast radius rather than a public origin's.
