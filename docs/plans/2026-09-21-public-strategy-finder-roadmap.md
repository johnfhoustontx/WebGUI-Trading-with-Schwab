# Strategy Finder on the public live screens: roadmap

**Date:** 2026-09-21
**Status:** Phase 0 in progress. D1, D3 and D4 decided 2026-09-21; D2 (Schwab data terms) is open.
**Ask:** publish the Strategy Finder (`/options/swing`) on `live.neuralstrike.co`.
A visitor types **one thing, a symbol**, and gets the Finder's results for it
under the app's standard filters. Every other control is fixed.

Background: this was assessed on 2026-09-13 and shelved. The points from that
assessment are carried forward here rather than re-derived.

---

## Why this is not just another `Screen` row

The twenty screens published so far are all **pure readers of data the stack
already produces**. The public process has four read-only layers (CLAUDE.md,
"The public live screens"): a Redis ACL user with no write commands,
`bus_client.set_read_only(True)`, a frozen settings store, and no control routes
at all. The Finder breaks that shape in three ways:

1. **It is request/response.** `cache:options:swing` is ONE symbol-agnostic slot
   holding whatever the owner last scanned. Mirroring it would publish the owner's
   last click, possibly days old (on 09-13 the Trade slot held OKLO from 09-10).
2. **A scan is expensive.** Live on 2026-09-14 a scan took 13 s (NVDA), 26 s
   (SPY) and 40 s ($SPX). It runs on the single serial `cmd:options` consumer, so
   one public scan delays paper creates, Calculator loads and dossiers queued
   behind it. A queued Calculator load waiting behind a $SPX scan outlasts its
   30 s overlay and shows a false timeout.
3. **"Type a symbol, get results" is an enqueue by definition.** On this origin
   an enqueue is refused at two layers, and removing either one is a change to
   the security model.

Decision D3 (any symbol a visitor types) makes point 3 unavoidable: the public
origin has to be able to ask for a scan. The roadmap therefore opens ONE
narrow write path, a single stream carrying a single validated field, and
keeps every other layer of the read-only model as it is.

---

## 1. Decisions

| # | Question | Decision (2026-09-21) | What it means for the build |
|---|---|---|---|
| D1 | Publish **named trade ideas** (a specific structure on a specific stock, with a score and a grade)? | **Yes.** | The page shows ranked candidates as the private Finder does. The header states the filters and the scan time. No disclaimer banner is added, matching the published market reports; the site footer already says paper only. |
| D2 | Schwab market-data terms for **republishing quotes** | **Open.** Owner to read the agreement (first read under Phase 0 progress). | Per-leg bid/ask/mark columns sit behind one config switch, `show_leg_quotes`, off until D2 is settled. Derived fields (credit %, max loss, PoP, score) show regardless. The same question applies to the Macro Board already live. |
| D3 | **Which symbols**? | **Anything a visitor types.** | On-demand scans are the core of the design (Phase 1), not an optional extra. |
| D4 | **Undefined-risk structures** in public output? | **Don't hide them.** | No per-row filter. Every row states its risk plainly: the page's existing "∞" max-loss cell, and an "Unlimited loss" chip on those rows. |

---

## 2. The fixed filters

The visitor edits nothing but the symbol, so every public scan runs **one pinned
parameter set**, defined once in `config/finder_public.toml` (the standing
configurable-by-default rule) and catalogued in `webgui/config_schema.py`:

| Parameter | Private default (`_SWING_DEFAULTS`) | Public pin | Reason |
|---|---|---|---|
| `dte_min` / `dte_max` | 0 / None (whole chain) | **0 / 90** | Cost and the chooser, now that D4 no longer argues for it. A whole chain costs 26–40 s, and past 30 expirations the scan answers with a chooser that needs a click the public page cannot offer. Tunable in config; widening it trades scan time for coverage. Side effect: the 1–2 year short straddles that topped SPY on 09-14 are outside this window. |
| `expiry_choice` | None (ask) | `ask_if_large=False` | Never answer with the chooser. |
| `put_d_min/max`, `call_d_min/max` | −0.20/−0.10, 0.10/0.20 | same | Already the app's standard. |
| `min_cr_fraction` | 0.10 | same | Same. |
| `families` | None (all seven) | None | D4: nothing hidden. |
| `earnings_mode` | `"flag"` (tag, keep) | same | A row open through a report shows the flag. |

`test_cross_tier_mirrors.py` pins the private defaults across tiers; the public
pin gets the same treatment, so the page's "standard filters" line cannot drift
from what the service ran.

---

## 3. Phased roadmap

### Phase 0: groundwork (no public change)

- Resolve D1–D4. (D2 is still open; it gates only the quote columns.)
- Measure the pinned scan **during RTH**: wall time, proxy calls, rows and
  payload per symbol. This sizes the daily budget and the queue (§5).

#### Phase 0 progress (2026-09-21)

- **Tool:** `tools/measure_finder_public.py` runs the real `compute.swing_scan`
  with the §2 pin and records wall time, proxy calls by endpoint, rows,
  unbounded-loss rows, short-put rows, payload size and the top of the ranking.
  It calls compute, never the handler, so it writes no cache key (pinned by
  `tools/tests/test_measure_finder_public.py`).
- **Smoke run, 07:20 CT, pre-market:** IWM took 11.1 s and 6 proxy calls
  (3 `/chains`, 1 `/quote`, 1 `/pricehistory`, 1 `/passthrough`) over 17
  expirations, and produced 0 rows. Pre-market marks fail the quality cut, so
  row counts need the session run.
- **Session run:** scheduled on the box for 09:05 CT (a transient
  `finder-phase0-measure` timer) over SPY, QQQ, $SPX, IWM, NVDA, AAPL, MSFT,
  AMD, TSLA and META. Output goes to `/tmp/finder_phase0/run-0905.{json,log}`.
- **Proxy baseline, 2026-09-18:** 130–155 requests a minute in the quiet
  minutes, 284 at the autoscan (the ceiling is 300). The GEX poll skipped at
  08:31, 08:53, 09:31, 09:45 and 09:51–52, and none at 09:04–09:12, which is
  why 09:05 is the test minute.
- **D2, first read (not legal advice):** secondary summaries of Schwab's
  Online Services Agreement say a client "will not redistribute or facilitate
  the redistribution of Market Information" to anyone Schwab has not
  authorized, and the Brokerage Account Agreement limits market data to
  personal, non-commercial use. Schwab's own page could not be fetched to
  confirm the wording. ⚠ **If that reading holds, it bears on screens already
  published**, not only the Finder: the Macro Board shows raw last prices and
  changes, while the Gamma boards show derived exposure figures. The owner
  should read the agreement itself. Derived-versus-raw is the question to take
  to it.

### Phase 1: the request path and the worker (Tier 2 + Redis ACL)

The one change to the public origin's security model, built and tested before
any page exists.

- **A dedicated stream `cmd:finder_public`.** The live ACL user gains write
  permission on **that key only**, using a Redis 7 write selector
  (`%W~cmd:finder_public`), so it still cannot write `cmd:options` or any cache
  key, and still has no `+publish`. (A public process that can publish can
  spoof repaint events to the private app.) The box runs Redis 7.0.15 (checked
  2026-09-21), and selectors arrived in 7.0.
- **`bus_client.set_read_only(True)` stays.** A new, separate function,
  `bus_client.request_public_scan(symbol)`, is the only enqueue this origin can
  make. It validates with `shared.symbols.clean_symbol`, writes `{symbol, ts}`
  and nothing else, and uses `XADD ... MAXLEN ~ N` so the stream cannot grow
  without bound. Tests pin that `request()` still refuses everything and that
  the new function cannot target another stream.
- **A separate consumer in options_svc**, its own thread, never the
  `cmd:options` consumer, so a 40 s scan cannot block a paper create or a
  Calculator load. One scan at a time.
- **Replay guard.** Consumer groups created at id 0 replay the whole backlog.
  That is the documented incident that burned a day's API budget. Entries older
  than a few minutes are acknowledged and dropped unscanned, reusing the
  `_is_stale_side_effect` age gate.
- **Refusals before the full scan**, in this order, each published as an
  outcome the page can word:
  1. the symbol fails `clean_symbol`: refused, no Schwab call;
  2. a fresh result exists (`swing_pub:<SYM>` younger than the TTL, e.g. 15
     minutes): served from cache, no Schwab call;
  3. the same symbol was requested in the last 60 s: deduplicated (the
     dossier's rule);
  4. outside the scan window (market hours by default; the 07:20 smoke run
     showed pre-market scans return nothing usable): refused, and the last
     cached result stays on screen;
  5. the daily scan budget is spent: refused;
  6. the symbol has no option expirations (one `/expirationchain` call):
     "no options listed".
- **Results** go to `cache:options:swing_pub:<SYMBOL>` with a TTL, plus
  `cache:options:finder_public_status`: the queue length, the scans used and
  left today, and the last outcome per symbol. The public page reads only
  these.
- **Observability.** The worker's degrades appear in `/health`, as every
  service's do. Its daily scan count appears beside the Schwab call counts in
  Settings, so public usage is visible in the private app.
- **Tests.** The ACL grant in the runbook; the only public write; the replay
  guard; each refusal in order; that the worker never touches `cmd:options` or
  `cache:options:swing`; and a Redis-driven end-to-end check on prod with the
  live ACL user before any page ships.

#### Phase 1 progress (2026-09-21)

Built and tested; not yet exercised on prod.

- `shared/public_scan.py` + `config/finder_public.toml` (+ Settings catalogue),
  `[windows.finder_public]` 08:40–15:00 CT.
- `bus_client.request_public_scan`: the one allowed write, AST-pinned.
- `make_app(extra_consumers=...)` and `services/options_svc/finder_public.py`,
  wired in `options_svc/app.py`.
- `handlers.finder_payload`, extracted from `swing_scan` unchanged.
- Runbook §2 step 4c: the ACL selector and its two-way verification.
- **Deferred to Phase 2:** the daily scan count beside the Schwab counts in
  Settings (the status view already carries it), and the queue position (the
  status view carries the busy symbol; a position needs the page to exist).
- **Independent review, same day.** No critical findings; the ACL scoping,
  the one write path and the unchanged private refactor held. Fixed before
  commit:
  - a symbol found to have no options is remembered for `negative_ttl_min`
    (240) instead of the 60 s dedup, so junk tickers cannot spend the budget
    one minute at a time;
  - `busy` records when it started and is dropped after 10 minutes or at the
    day's rollover, so a crash mid-scan cannot leave it set;
  - a request stamped more than 30 s in the future is refused as expired;
  - public scan failures count under `options.finder_public_*` in `/health`,
    not the owner's `options.swing_*`;
  - a failure writing the result after a good scan is an `error` that still
    clears `busy`;
  - the cost comments no longer claim the 90-day cap avoids the expiry
    chooser: every expiration in range is scanned. (This bullet first said
    ~60 for daily-expiry names; measured at 09:05, SPY and QQQ scan 18 and
    $SPX 35.)
  Carried into Phase 2 (below): the per-visitor limit, what the status view
  may show, and repaint churn.

**Exit:** on prod, the live ACL user can add to `cmd:finder_public` and is
refused on `cmd:options` and every `cache:` key. A request for SPY during the
session produces `swing_pub:SPY`. A request for a nonsense symbol spends no
Schwab call.

### Phase 2: the public page (Tier 1)

- **The per-visitor limit ships WITH the input box** (moved from Phase 3 on
  review). Dedup is per symbol and any well-formed ticker is accepted, so
  without it one visitor can still fill the queue: at 15–25 s a scan for a
  daily-expiry name, 8–10 queued symbols push every later request past the
  180 s wait limit.
- **The status view is public data; the page must not show its `last` map.**
  It lists every symbol anyone searched. The page reads only its own symbol's
  entry, plus the budget, window and busy flag.
- **Repaint churn.** Every refusal rewrites the status view and publishes an
  event, so every open public page repaints once per request anywhere. The page
  should repaint only when its own symbol's entry, the budget or the busy flag
  changed.
- `swing.render(public=False)`: an optional keyword like
  `gamma.render(symbol=, view=)`, defaulting to today's behaviour so the private
  route is unchanged.
- With `public=True`:
  - **A symbol field and one button.** Enter or Scan calls
    `request_public_scan` and nothing else. The page then watches
    `swing_pub:<SYM>` and `finder_public_status`. While it waits it shows the
    queue position, then the answer: results, or the refusal in words.
  - **Not built**: the DTE, delta and min-credit inputs; the expiry-choice
    buttons; **Paper**; **Calculator**; the Expected Move hand-off. Apply the
    rule the Gamma screens follow: a control that cannot work is not drawn.
  - `may_enqueue(public)` gates every other `bus_client.request` site in the
    module, with an AST test proving every enqueue site is gated. This is the
    `gamma.may_enqueue` pattern.
  - Header: the symbol, "Scanned 10:42 CT", and the fixed filters in words.
  - Every row with unlimited loss is labelled (D4). Quote columns follow
    `show_leg_quotes` (D2).
  - Checklist chips that read shared views stay. The **Paper book** line reads
    the owner's `ledger_caps` and is dropped in public mode.
- `live_screens.py`: `Screen("finder", "/finder", "Strategy Finder",
  "options.swing", "/options/swing", kwargs={"public": True})`, plus a tile in
  `deploy/site/live.html`. The thumbnail capture shows whatever the page draws
  with no symbol typed, e.g. the last cached SPY result.
- Verify with `tools/ui_harness.py`, then live over `live.neuralstrike.co`.

#### Phase 2 progress (2026-09-21)

Built, tested and walked in a local harness; not yet promoted.

- The page is `pages/options/finder_live.py`, reached through
  `swing.render(public=True)`. A separate module rather than a flag threaded
  through the private page's ~1,200 lines: every owner control there would
  have needed its own gate. What the visitor SEES still comes from the private
  page's builders, so the two cannot drift in content.
- The per-visitor limit, the own-symbol-only status read and the
  poll-only-while-waiting rule are in, each pinned by a test.
- **Not in Phase 2:** the Trade detail panel (its checklist reads the owner's
  ledger; it could return without that line), and the daily scan count in
  Settings.

**Exit:** a visitor on `/finder` types a symbol and gets results or a worded
refusal. The read-only refusal counter for everything except the one allowed
write stays at zero.

### Phase 3: abuse limits and polish

- **Per-visitor limit**: moved to Phase 2. A few scans per client per hour,
  keyed on the last `X-Forwarded-For` hop Caddy stamps, held in memory only (no
  IP address is written to Redis or to disk).
- **Row cap** for the public table (e.g. the best 5 per strategy type), for
  readability and for `webgui_live`'s 1 GB memory cap.
- **Warm cache (optional).** Pre-scan a short list, e.g. yesterday's most
  requested symbols plus SPY, QQQ and IWM, shortly after the open, so the
  common symbols answer instantly.
- Empty, stale and after-hours wording in `pages/copy.py`; a User Guide
  section; a `page_help.py` entry; docs in `webgui-routes.md`, CLAUDE.md's
  public-screen section and the CHANGELOG.

#### Phase 3 progress (2026-09-21)

Built and tested: the row cap (`rows_per_type`, trimmed at write), the 09:08
warm-up through the worker, the Settings usage row, and the closed-hours line.
The User Guide section landed with Phase 2. Still open: a `page_help.py` entry
(the public origin draws no help control today), and the Trade detail panel
without its ledger line.

### Phase 4 (optional): an edge rate limit

**Built off, 2026-09-21.** `config/edge.toml` emits the limit for the public
host when turned on; installing the module-bearing Caddy is a root step in the
runbook's "Edge rate limit" section, not yet taken.

Caddy has no rate limit today; that needs an `xcaddy` build with the
rate-limit module. It would stop a flood at the edge rather than in the
public process. The daily budget already bounds the Schwab cost without it.

---

## 4. Risks

- **The public origin can now write.** One stream, one validated field, a
  bounded length, enforced by the Redis server rather than by this process.
  The worst an attacker can do is spend the day's public scan budget. That
  denies other visitors, not the owner: separate stream, separate worker,
  separate budget.
- **Dev reaches prod's proxy.** Command handlers are not suppressed in a dev
  environment, so a request to dev's public origin would scan through prod's
  proxy. Dev's public origin is not fronted by the edge, so only a local
  request can reach it; the budget still bounds it.
- **Load on the proxy.** Private and public Finder scans can now run at the
  same time, on separate threads, where before every Finder scan was serial on
  `cmd:options`. One public worker makes 5–8 calls per scan (measured, §5) at
  about 11 s a scan, roughly 0.5 requests a second against the headroom in §5. If the
  session run shows GEX skips, pause the worker during the autoscan minutes
  (:00–:03, :15–:18, …) and let the queue wait.
- **Scoring credibility.** Under D4, undefined-risk rows show, and their
  ranking has never been measured against outcomes. The 90-day cap keeps out
  the long-dated short straddles seen on 09-14, but short strangles, straddles
  and naked calls inside 90 days rank alongside spreads.
- **Memory on the public process.** Capped at 1 GB, with each anonymous GET
  holding ~619 KB for ~70 s. Keep the row cap.
- **Schwab terms (D2).** Open, and wider than this feature.

## 5. Cost arithmetic

**Measured on prod, 2026-09-21 09:05 CT** (`tools/measure_finder_public.py`,
the public pin, during the session):

| Symbol | Wall | Calls | Expirations | Rows | Unlimited loss | Payload |
|---|---|---|---|---|---|---|
| SPY | 10.1 s | 6 | 18 | 90 | 0 | 158 KB |
| QQQ | 8.7 s | 6 | 18 | 67 | 0 | 117 KB |
| $SPX | 36.0 s | 8 | 35 | 47 | 0 | 84 KB |
| IWM | 10.3 s | 6 | 17 | 81 | 7 | 147 KB |
| NVDA | 5.9 s | 5 | 13 | 80 | 0 | 141 KB |
| AAPL | 4.3 s | 5 | 13 | 62 | 6 | 113 KB |
| MSFT | 10.9 s | 5 | 13 | 17 | 0 | 30 KB |
| AMD | 10.3 s | 5 | 13 | 42 | 3 | 79 KB |
| TSLA | 9.8 s | 5 | 13 | 96 | 9 | 178 KB |
| META | 6.7 s | 5 | 13 | 29 | 2 | 53 KB |
| **Total** | **113 s** | **56** | | **611** | **27** | **1.1 MB** |

- **Per scan: 11.3 s and 5.6 calls on average**; $SPX is the outlier at 36 s.
  No short puts in any result.
- **Proxy load while it ran:** 166 and 186 requests a minute at 09:05–09:06
  against Friday's 155 and 136 at the same minutes, far under the 300 ceiling.
  **No GEX minute was skipped during the run** (the day's one skip, at 09:01,
  was the autoscan's, before it started).
- **The daily budget of 200** is about 1,100 Schwab calls and about 38 minutes
  of worker time, against a stack already making 68–76k calls a day. It could
  go higher on cost; it is the queue that binds first.
- **The queue:** one worker at ~11 s clears ten distinct symbols in about two
  minutes, inside the 180 s wait limit, so the per-visitor limit (10 an hour)
  is what keeps one visitor from filling it.
- **Payloads before the five-per-type trim** ran 30–178 KB; the SPY result the
  worker stored at 08:57 kept 48 rows and held back 37.
- ⚠ The first version of the tool read a `score` field that does not exist
  (the Finder's is `composite_score`), so that run's "top rows" were unranked
  and are not reported here. Fixed.

The estimates this section held before the run:

- **Per scan** (smoke run, IWM, pre-market): 11.1 s, 6 proxy calls.
- **Throughput:** one worker at ~11 s a scan does ~5 scans a minute at most. A
  visitor behind four others waits about a minute, which is why the page shows
  the queue position.
- **Budget:** 200 scans a day is ~1,200 Schwab calls, against the 68–76k a day
  the stack already uses. Set in config; the right number comes from the
  session measurement and real demand.
- **Proxy headroom:** 130–155 requests a minute in quiet minutes against a
  ceiling of 300, but only ~16 to spare at the autoscan peak (284).

## 6. Files touched

| File | Change |
|---|---|
| `config/finder_public.toml` *(new)* | pin, TTL, dedup window, scan window, daily budget, per-visitor limit, `show_leg_quotes` |
| `webgui/config_schema.py` | catalogue the new keys |
| `docs/dev-prod-environments.md` | the ACL change for the live user |
| `webgui/bus_client.py` | `request_public_scan`, the one allowed write |
| `services/options_svc/` | the `cmd:finder_public` consumer, the refusals, `swing_pub_key`, the status view |
| `webgui/pages/options/swing.py` | `render(public=)`, `may_enqueue`, public mode |
| `webgui/live_main.py` | the per-visitor limit |
| `webgui/live_screens.py` · `deploy/site/live.html` | the screen and its tile |
| `shared/tests/test_cross_tier_mirrors.py` | public pin ↔ page mirror |
| docs | User Guide, `page_help.py`, `webgui-routes.md`, CLAUDE.md, CHANGELOG |
