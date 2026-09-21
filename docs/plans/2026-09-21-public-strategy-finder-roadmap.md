# Strategy Finder on the public live screens: roadmap

**Date:** 2026-09-21
**Status:** proposed, not built. Two product decisions (§1) gate everything after Phase 0.
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

So the roadmap below keeps the read-only model intact for as long as possible.
It moves to on-demand scans only as an explicit, separately-gated step.

---

## 1. Decisions needed before building (owner's call)

| # | Question | Why it matters | Recommendation |
|---|---|---|---|
| D1 | Publish **named trade ideas** (a specific spread on a specific stock, with a score and a grade)? | The existing screens publish market structure and signals; this publishes "sell this 30-delta put spread on X". That is a different kind of public statement. | Decide explicitly. If yes, carry the site's existing paper-only framing into the page header. |
| D2 | Schwab market-data terms for **republishing raw option quotes** (bid/ask/mark per leg) | The Finder's rows carry per-leg prices. The Gamma screens publish derived aggregates; this would republish quotes. | Check the Schwab API terms before Phase 2. If raw quotes may not be shown, the public table drops the per-leg price columns and keeps derived fields (credit %, PoP, score). |
| D3 | **Which symbols**: a curated list, or anything a visitor types? | Sets the cost model and the whole architecture (§3 vs §4). | Start curated (Phases 1–3). Treat free-text on-demand as a separate decision (Phase 4). |
| D4 | **Undefined-risk structures** in public output? | On 09-14 SPY's top-ranked rows were 1–2 year **short straddles** (scores 83/82/78, "Strong"). That ranking is unmeasured against outcomes, and it is the first thing a visitor would see. | Exclude undefined-risk families from the public scan and cap DTE (see Phase 1). |

---

## 2. What "the filtering criteria" become

The visitor edits nothing but the symbol, so the public scan is **one pinned
parameter set**, defined once in config (the standing configurable-by-default
rule) and catalogued in `webgui/config_schema.py`:

| Parameter | Private default (`_SWING_DEFAULTS`) | Public pin (proposed) | Reason |
|---|---|---|---|
| `dte_min` / `dte_max` | 0 / None (whole chain) | **0 / 90** | A whole chain triggers the >30-expiration chooser, which needs a click. It also costs 26–40 s, and the long-dated short straddles live in it (D4). |
| `expiry_choice` | None (ask) | **"Next 90 days"** | Never answer with the chooser: the public page has no buttons to pick one. |
| `put_d_min/max`, `call_d_min/max` | −0.20/−0.10, 0.10/0.20 | same | Already the app's standard. |
| `min_cr_fraction` | 0.10 | same | Same. |
| `families` | None (all seven) | None, **plus a per-row defined-risk filter** | D4. ⚠ A family filter cannot do this: the build groups mix both kinds (STRADDLE builds long AND short straddles, DIRECTIONAL builds naked `SHORT_PUT`/`SHORT_CALL` beside long options). Drop rows by their own risk, publish-side, and count them as `undefined_risk_hidden`, never folded into `filtered_out`. The same reason `vol_filtered` is its own field. |
| `earnings_mode` | `"flag"` (tag, keep) | same | A public row open through a report must show the flag. |

`test_cross_tier_mirrors.py` already pins the private defaults across tiers;
the public pin gets the same treatment in Phase 1.

---

## 3. Phased roadmap

### Phase 0: groundwork (no public change)

- Resolve D1–D4.
- Measure the scan at the Phase 1 pin (0–90 DTE, defined risk) on a handful of
  symbols **during RTH**. The 09-14 numbers were for the whole chain, and the
  estimate here is roughly half. Record wall time, `/chains` calls and payload
  size per symbol.
- Decide the curated list size from that measurement (§5 has the arithmetic).

### Phase 1: scheduled public scans (Tier 2 only)

Follow the Gamma precedent: **the service publishes, per symbol, on a schedule;
the public page only reads.**

- New `config/finder_public.toml` (+ catalogue entries): the symbol list, the
  pinned parameters from §2, and the schedule.
- New `[slots.finder_public]` in `config/sessions.toml`, e.g. twice a day
  (after the open settles, ~09:05 CT, and midday), placed **off the quarter
  hours**. Each 15-minute mark belongs to the autoscan. Before choosing the
  minute, read the proxy access log for that time, as the 2026-09-16 rule
  requires.
- A new **scheduler branch** in `options_svc/scheduler.py`, **not a
  `cmd:options` command**. It runs through `launch_branches`, so it delays
  only itself and never queues ahead of a paper create or a Calculator load.
  It loops the list calling `compute.swing_scan` with the public pin.
- Publish each result to **`cache:options:swing_pub:<SYMBOL>`** plus an event
  (the `gamma_pub_key` pattern). Never write the private `cache:options:swing`
  slot. Validate `<SYMBOL>` through `shared.symbols.clean_symbol`.
- A `cache:options:swing_pub_index` view: the published symbols, the time each
  was last scanned and its row count. The page uses it for its symbol list and
  "last scanned" line.
- Per-symbol guard via `_degrade.degraded`: one symbol's failure costs only
  that symbol.
- Tests: the scheduled branch never enqueues on `cmd:options`, no undefined-risk row reaches a published key, the published
  key per symbol, the index view, the pin mirrors the TOML, and an unmapped
  symbol is refused.

**Exit:** after a trading day, `swing_pub:*` keys exist for every listed
symbol, and the GEX poll logged no `still running` skips at the scan minutes.

### Phase 2: the pinned public page (Tier 1)

- `swing.render(symbol=None, public=False)`: an optional keyword like
  `gamma.render(symbol=, view=)`, defaulting to today's behaviour so the private
  route is unchanged.
- With `public=True`:
  - **Symbol field only.** It is a `ui.select` with `with_input` over
    `swing_pub_index`, so a visitor can type to filter but can only land on a
    published symbol. It changes which `swing_pub:<SYM>` key the page reads; it
    sends nothing. (A free-text box that accepts an unlisted symbol belongs to
    Phase 4.)
  - **Not built**: the DTE, delta and min-credit inputs; Scan; the
    expiry-choice buttons; **Paper**; **Calculator**; the per-row
    Expected Move hand-off. Apply the rule the Gamma screens follow: a
    control that cannot work is not drawn.
  - A `may_enqueue(public)` predicate gating every `bus_client.request` site in
    the module, plus an AST test that walks the source and proves every enqueue
    site is gated. This is the `gamma.may_enqueue` pattern.
  - Header line: "Scanned 09:05 CT · next 12:35 CT · standard filters (0–90
    days, defined risk)". The visitor needs to know the results are a schedule,
    not a live scan.
  - The checklist chips read `options:matrix` etc. These are shared views and
    are safe. The **Paper book** line reads the owner's `ledger_caps`: drop it
    in public mode, since it describes the owner's book.
- `live_screens.py`: `Screen("finder", "/finder", "Strategy Finder",
  "options.swing", "/options/swing", kwargs={"public": True})`. A new tile goes
  in `deploy/site/live.html`, and the capture tool picks it up automatically.
- Verify with `tools/ui_harness.py` over prod's real `swing_pub:*` payloads,
  as this session did for the Gamma boards.

**Exit:** `/finder` serves over the live origin. `bus_client`'s read-only
refusal counter stays at zero across a walk of every symbol, which is the proof
that nothing tries to enqueue.

### Phase 3: polish and guardrails

- Empty and stale states: "not scanned yet today", and after hours "Last
  session's scan; index open interest reads zero after hours". Use the
  existing wording in `pages/copy.py`.
- A row cap for the public table (e.g. the best 5 per strategy type) so the
  page reads as a short list, not a 160-row dump.
- A manual page (User Guide, "The public live screens") and a
  `page_help.py` entry. Plus docs: `webgui-routes.md`, CLAUDE.md's public-screen
  section, the CHANGELOG.

### Phase 4 (optional, separately decided): on-demand scans for any symbol

This is the only phase that changes the public origin's security model. Build
it only if Phases 1–3 prove the demand. The minimum safe shape:

- **A dedicated stream `cmd:finder_public`.** Give the live ACL user
  `+xadd` on **that key only**. It must never cover `cmd:options` and never
  `+publish` (a public process that can publish can spoof repaint events to the
  private app). Update `bus_client.set_read_only` to allow exactly that one
  command type, and pin both with tests.
- **A separate consumer** in options_svc, not the `cmd:options` consumer, so a
  40 s scan never blocks the owner's commands. One scan at a time.
- **Refusals before any Schwab call:** `clean_symbol`; the symbol must be
  optionable (reuse the dossier's quote leg); a per-symbol **dedup** (a result
  younger than the TTL is served from cache, as the dossier's 15-minute TTL and
  60 s dedup do); a **queue-depth cap** (refuse, don't queue, past N pending);
  and a **daily budget** of on-demand scans in config, with the refusal shown
  to the visitor.
- **Per-client limiting** cannot be trusted inside NiceGUI alone. It keys on
  the last `X-Forwarded-For` hop Caddy stamps (the `X-Edge 1` rule). The edge
  has no rate limit today (that needs an `xcaddy` build).
- Results go to the same `swing_pub:<SYM>` keys with a short TTL, so the
  Phase 2 page renders them unchanged.

---

## 4. Risks carried into every phase

- **Load on the proxy.** Each scan fetches chains 4 expiries at a time through
  the proxy's shared 5 req/s. A burst at the wrong minute pushes the 1-minute
  GEX poll past its slot, and the heatmap loses that minute. Mitigation:
  off-quarter-hour slots, measured before scheduling.
- **Memory on the public process.** `webgui_live` is capped at 1 GB, and each
  anonymous GET holds ~619 KB for ~70 s. A Finder payload of ~130–320 KB per
  render adds to that. Keep the public row cap (Phase 3).
- **Scoring credibility.** The long-dated short-straddle ranking (D4) is an
  open, unmeasured question. The public pin avoids it but does not answer it.
- **Staleness is the honest cost of Phases 1–3.** Results are as old as the
  last slot, and the header has to say so.

## 5. Cost arithmetic (to confirm in Phase 0)

- **Time:** with N symbols at ~12 s each (estimated for the 0–90 DTE pin,
  roughly half the whole-chain figures), 20 symbols is ~4 minutes per pass on a
  scheduler branch that blocks nothing but itself.
- **Schwab calls:** one expiration-list call plus ceil(expiries ÷ 8) chain
  runs. At ~12 expiries in 90 days that is ~3 calls per symbol. 20 symbols × 2
  passes ≈ 120 calls/day, against a budget running 68–76k/day. Negligible.
- **Redis:** 20 keys × ~150 KB, written twice a day.

## 6. Files touched (Phases 1–3)

| File | Change |
|---|---|
| `config/finder_public.toml` *(new)* | symbol list, pinned parameters |
| `config/sessions.toml` | `[slots.finder_public]` |
| `webgui/config_schema.py` | catalogue the new keys |
| `services/options_svc/scheduler.py` | the scheduled branch |
| `services/options_svc/handlers.py` | `swing_pub_key`, `publish_finder_public`, the index view |
| `webgui/pages/options/swing.py` | `render(symbol=, public=)`, `may_enqueue`, public mode |
| `webgui/live_screens.py` · `deploy/site/live.html` | the screen and its tile |
| `shared/tests/test_cross_tier_mirrors.py` | public pin ↔ page mirror |
| docs | User Guide, `page_help.py`, `webgui-routes.md`, CLAUDE.md, CHANGELOG |
