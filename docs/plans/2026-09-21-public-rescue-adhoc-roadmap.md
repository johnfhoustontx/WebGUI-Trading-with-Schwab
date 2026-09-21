# Rescue ad-hoc form on the public live screens: blueprint

**Date:** 2026-09-21
**Status:** Proposal. Nothing built. Decisions D0–D4 are open.
**Ask:** publish Rescue's **ad-hoc trade form** on `live.neuralstrike.co`. A
visitor describes a trade they hold elsewhere and gets the ranked,
commission-aware list of repairs the private page shows.

**Precedent:** the public Strategy Finder
([roadmap](2026-09-21-public-strategy-finder-roadmap.md)), the only public
screen that asks the stack to do work. This blueprint copies its shape wherever
it can and says where it has to differ.

---

## 1. What gets published, and what never does

The private `/options/rescue` is two screens on one page:

| Part | Reads | Public? |
|---|---|---|
| **At-risk board**: the owner's tested paper and captured positions, with Apply | `cache:options:paper_account`, `cache:options:captured`, `cache:options:rescue:<id>` | **Never.** It is the owner's book, and Apply writes to it (`rescue_apply`). |
| **Ad-hoc form**: symbol, strategy, legs, then Compute | `cache:options:calc_chain`, `cache:options:rescue:adhoc` | **Yes**, rebuilt as below. |

The ad-hoc form's output is already advisory-only
(`compute_rescue_adhoc` forces `apply_kind="advisory"` on every candidate), so
the published result is the same thing the owner sees, minus nothing.

## 2. Why this is not just another `Screen` row

The same three reasons the Finder gave, plus one of its own:

1. **Both of the form's keys are single shared slots.** `calc_chain` is also the
   owner's Calculator's chain, and `rescue:adhoc` is one key for every ad-hoc
   result. A public copy reading them would show visitors the owner's last
   symbol and trade, and the next visitor's Compute would overwrite the
   previous visitor's answer.
2. **It is request and response.** Load, a far expiration pick, and Compute are
   three enqueues on `cmd:options`, and the public origin refuses all enqueues
   at two layers (the Redis access-control user and
   `bus_client.set_read_only`).
3. **Work on `cmd:options` delays the owner.** That consumer is serial. A
   public load or compute would queue behind, and in front of, the owner's
   paper creates and Calculator loads.
4. **The input is a whole trade, not one symbol.** The Finder's public write
   carries one field that `clean_symbol` validates. A rescue request carries a
   strategy, up to four strikes, an expiration, a quantity and an entry
   credit. Every one of those fields has to be validated in the shared module
   before it is written, or the one narrow write path stops being narrow.

## 3. Decisions for the owner

| # | Question | Recommendation | Why |
|---|---|---|---|
| **D0** | Open a **second** public write path? CLAUDE.md says the public origin has exactly one write. | **Yes, as its own stream**, `cmd:rescue_public`, with its own access-control selector, worker and daily budget. | Keeps each path separately revocable, and a flood on one cannot starve the other. Folding into `cmd:finder_public` would break that stream's AST-pinned one-field contract. |
| **D1** | Does the form load a **quoted chain** like the private page? | **No. It loads a strikes-only ladder**: expirations and each expiration's strikes, with no bid, ask or mark. | The private chain is 0.7–1.3 MB per symbol and full of quotes. The form needs quotes for nothing: the visitor types their own entry prices, and the rescue compute reprices live on the server. A ladder is a few KB and publishes no market data. |
| **D2** | Show **per-leg fill prices** in the results? Each card lists legs as "SELL PUT 500 @1.20". | Behind the Finder's existing `show_leg_quotes` switch, off until the Schwab terms question (Finder D2) is settled. The net credit or debit, commission, and resulting risk show regardless. | Same question the Finder already has open; one switch answers both. |
| **D3** | Which **strategies**? | The private form's supported list (credit spreads, iron condor, single options, debit verticals, condors, butterflies). **No stock legs**, as on the private form. | Already validated server-side by `compute_rescue_adhoc`; stock legs are excluded there for a reason documented in CLAUDE.md. |
| **D4** | Hours? | **Market hours only**, from a new `[windows.rescue_public]` in `config/sessions.toml`. Outside it the form still builds, and Compute says when it opens. | Off hours the reprice falls back to stale marks and the proximity trigger reads a stale underlying, so a repair list would look confident and be wrong. |

## 4. The build, in phases

### Phase 0: measure (no public change)

- A tool like `tools/measure_finder_public.py`: run the real
  `compute_rescue_adhoc` over ten representative trades (a tested SPY put
  spread, an iron condor on QQQ, a far-dated naked put, a $SPX spread) during
  the session. Record wall time and proxy calls by endpoint. It calls compute,
  never the handler, so it writes no cache key.
- **Estimated before measuring:** one reprice chain, one light gamma chain, and
  the roll candidates' later-expiration chains, so roughly 3–6 calls and a few
  seconds per compute. The measurement sets the daily budget; this estimate
  does not.
- Measure the ladder build too: expected to be one `/expirationchain` plus one
  `/chains` per requested expiration, trimmed to strikes.

### Phase 1: the request path and the worker (shared, services, Redis)

- **`shared/public_rescue.py`** (pure; joins the Tier-1 allow-list beside
  `shared.public_scan`): the stream name, the two command types, the result
  keys, the config accessors, and **the validators**:
  - `ladder_command(symbol, expiry=None)`: symbol through `clean_symbol`,
    expiry through an ISO-date check.
  - `rescue_command(spec)`: strategy from a fixed enum; strikes as finite
    positive numbers; expiration an ISO date; quantity an integer 1–100; entry
    credit a finite number. Unknown keys are dropped, not passed through.
  - `spec_key(spec)`: a hash of the normalized spec. The result key is
    content-addressed, so two visitors entering the same trade share one
    compute, and the key name reveals nothing about the trade.
- **`bus_client.request_public_rescue(...)`** beside `request_public_scan`:
  takes validated fields, never a stream name or a command type, and writes
  with `MAXLEN ~ N`. The same AST test that pins the Finder's write pins this
  one. `request()` stays refused for every domain.
- **Access control:** the `live` Redis user gains `%W~cmd:rescue_public
  +xadd` beside the existing Finder selector, and nothing else. Verified both
  ways (it can add there; it is refused on `cmd:options` and every `cache:`
  key), as runbook §2 step 4c already does for the Finder.
- **`services/options_svc/rescue_public.py`**, registered through
  `make_app(extra_consumers=...)`, never the `cmd:options` consumer. One job at
  a time. Before any Schwab call, in this order, each an outcome the page can
  word:
  1. fails validation: refused;
  2. stale (the replay guard, reusing `_is_stale_side_effect`): dropped;
  3. a fresh result under that spec key or ladder key exists: served, no call;
  4. the same spec was asked for in the last 60 seconds: deduplicated;
  5. outside the window: refused;
  6. the day's budget is spent: refused;
  7. the expiration is not listed for the symbol: refused.
- **Results:**
  - `cache:options:rescue_pub_ladder:<SYMBOL>`: expirations plus strikes for
    the expirations fetched so far, merged per expiration, 60-minute expiry.
    Keyed by symbol and shared by every visitor on that symbol.
  - `cache:options:rescue_pub:<spec_key>`: the advisory, 10-minute expiry.
  - `cache:options:rescue_public_status`: budget used and left, window open,
    busy. **No per-spec or per-symbol history.** The Finder's review found its
    status view listed every symbol anyone searched; a list of trades people
    hold is more sensitive than that, so it is never built.
- **Observability:** degrades count under `options.rescue_public_*` in
  `/health`, and the daily count appears beside the Finder's in Settings.
- **Nothing here logs a spec with a client address.** The per-visitor limit
  stays in memory in the public process, as the Finder's does.

**Exit:** on prod, a Redis-driven request for a tested SPY put spread produces
an advisory under its spec key; a request with a bad strike spends no Schwab
call; the `live` user is refused on `cmd:options`.

### Phase 2: the public page (Tier 1)

- `rescue.render(public=False)`: with `public=True` it hands off to a new
  **`pages/options/rescue_live.py`** before building anything, the way
  `swing.render(public=True)` hands off to `finder_live.py`. The at-risk board,
  its polls and its reads of the owner's book are never constructed, rather
  than hidden.
- What the visitor sees comes from the private page's own pieces, so the two
  cannot drift in content:
  - the **leg editor** in `row` layout, fed the ladder:
    `expiries_for` = expirations with strikes loaded, `listed_expiries_for` =
    every listed expiration, `on_expiry_needed` = request that expiration's
    strikes. That wiring is exactly what shipped on 2026-09-21
    (`038e1cf`) for the private form, so a far pick on one leg already
    behaves correctly here;
  - `adhoc_spec_from_legs` to build the spec;
  - the private page's card builder for the results, which already renders
    advisory cards with no Apply.
- **Not built:** Apply, Paper, the hand-offs to the Calculator and Expected
  Move, the Contracts multiplier's link to any account, and the at-risk board.
  A control that cannot work is not drawn.
- `may_enqueue(public)` gates every other `bus_client.request` site in
  `rescue.py`, with an AST test proving each one is gated (the
  `gamma.may_enqueue` pattern).
- **Per-visitor limit** from `webgui/visitor_limit.py`, its own counters: e.g.
  20 computes and 60 ladder requests an hour per client.
- Poll only while waiting, and repaint only on the visitor's own spec key,
  ladder key, or a change in budget, window or busy.
- `live_screens.py`: `Screen("rescue", "/rescue", "Rescue", "options.rescue",
  "/options/rescue", kwargs={"public": True})`, and a tile on
  `deploy/site/live.html`. The thumbnail shows the empty form with a SPY put
  spread template, not a result.
- **Site navigation:** Rescue already sits in the site nav's **Tools** menu
  as "Coming soon" (shipped 2026-09-21), beside Strategy Finder and the
  Calculator. Publishing the screen turns that entry into a link to
  `/rescue` in all four navs; `deploy/tests/test_site.py` fails until it
  does. Nothing is added to the top-level nav.
- Verify in the local harness first, then live.

### Phase 3: polish and documentation

- Wording in `pages/copy.py` for every refusal and for closed hours.
- User Guide section, `docs/webgui-routes.md`, the CHANGELOG, and CLAUDE.md's
  public-screen section: twenty-two screens, and the "exactly ONE write"
  sentence becomes two, each named.
- If Phase 0 shows gamma-minute skips while it runs, pause the worker during
  the autoscan minutes, as the Finder roadmap already allows.

## 5. Risks

- **A second write path.** Bounded the same way as the first: one stream,
  validated fields, bounded length, enforced by the Redis server. The worst
  case is a spent daily budget, which denies other visitors, not the owner.
- **The input surface is wider.** Strikes and credits are numbers a visitor
  chooses. Every one is validated in the shared module on the way in and
  again by the worker, and `compute_rescue_adhoc` is already fully defensive.
  A NaN, an infinity or a boolean must be refused at the first layer; that is
  the repo's most-repeated bug class, so it gets its own tests.
- **Advice credibility.** The ranking has not been measured against outcomes
  for trades held outside the app. The page says what the list is: repairs
  ranked by the app's rules, paper only, as the site footer already does.
- **Visitors' positions are sensitive.** Content-addressed keys, no status
  history, no logging with addresses, short expiry on results.
- **Schwab terms (D2).** Open, and shared with the Finder and the Macro Board.

## 6. Files touched

| File | Change |
|---|---|
| `config/rescue_public.toml` *(new)* + `webgui/config_schema.py` | budget, result and ladder expiry, dedup window, per-visitor limits |
| `config/sessions.toml` | `[windows.rescue_public]` |
| `shared/public_rescue.py` *(new)* | stream, commands, validators, keys |
| `webgui/bus_client.py` | `request_public_rescue` |
| `services/options_svc/rescue_public.py` *(new)*, `app.py`, `compute.py` | the worker, the ladder builder |
| `webgui/pages/options/rescue.py`, `rescue_live.py` *(new)* | `render(public=)`, `may_enqueue`, the public page |
| `webgui/live_screens.py`, `deploy/site/live.html` | the screen and its tile |
| `docs/dev-prod-environments.md` | the access-control selector |
| docs | User Guide, `webgui-routes.md`, CLAUDE.md, CHANGELOG |
