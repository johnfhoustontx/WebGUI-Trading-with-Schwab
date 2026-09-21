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

**Exit:** on prod, the live ACL user can add to `cmd:finder_public` and is
refused on `cmd:options` and every `cache:` key. A request for SPY during the
session produces `swing_pub:SPY`. A request for a nonsense symbol spends no
Schwab call.

### Phase 2: the public page (Tier 1)

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

**Exit:** a visitor on `/finder` types a symbol and gets results or a worded
refusal. The read-only refusal counter for everything except the one allowed
write stays at zero.

### Phase 3: abuse limits and polish

- **Per-visitor limit** in the public process: a few scans per client per
  hour, keyed on the last `X-Forwarded-For` hop Caddy stamps. Held in memory
  only: no IP address is written to Redis or to disk. A restart resets it,
  which is acceptable because the daily budget is the hard limit.
- **Row cap** for the public table (e.g. the best 5 per strategy type), for
  readability and for `webgui_live`'s 1 GB memory cap.
- **Warm cache (optional).** Pre-scan a short list, e.g. yesterday's most
  requested symbols plus SPY, QQQ and IWM, shortly after the open, so the
  common symbols answer instantly.
- Empty, stale and after-hours wording in `pages/copy.py`; a User Guide
  section; a `page_help.py` entry; docs in `webgui-routes.md`, CLAUDE.md's
  public-screen section and the CHANGELOG.

### Phase 4 (optional): an edge rate limit

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
- **Load on the proxy.** One worker, at about 6 calls per ~11 s (the smoke
  run), is roughly 0.5 requests a second against the headroom in §5. If the
  session run shows GEX skips, pause the worker during the autoscan minutes
  (:00–:03, :15–:18, …) and let the queue wait.
- **Scoring credibility.** Under D4, undefined-risk rows show, and their
  ranking has never been measured against outcomes. The 90-day cap keeps out
  the long-dated short straddles seen on 09-14, but short strangles, straddles
  and naked calls inside 90 days rank alongside spreads.
- **Memory on the public process.** Capped at 1 GB, with each anonymous GET
  holding ~619 KB for ~70 s. Keep the row cap.
- **Schwab terms (D2).** Open, and wider than this feature.

## 5. Cost arithmetic (confirm with the Phase 0 run)

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
