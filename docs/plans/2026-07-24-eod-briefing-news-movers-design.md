# EOD briefing + live news + notable movers across all Gamma briefings — design (2026-07-24)

## Problem

The 4×/day scheduled Gamma-Analyze briefings are all **forward-looking**. The
**close** slot fires at **14:58 CT — 2 minutes before the SPY/QQQ cash close** — yet
its whole schema is "into the close / what to do over the next few hours" ("fade rips
into the call walls, buy dips at the put walls"). Two minutes before the bell that
advice is useless.

The user wants the final briefing of the day to instead be a **retrospective**: what the
market actually did through the session (highlights/events, macro drivers, notable
individual stock moves) **plus what to prepare for the next session** — generated **after**
the 15:00 CT cash close (≈15:15 CT). Separately, the user wants the three **intraday**
briefings enriched with the same two new inputs: **notable individual stock moves** and
the **day's macro drivers** (live news).

## Decisions (locked in brainstorming)

1. **Repurpose the close slot** (no 5th briefing): move `close` from **14:58 → 15:15 CT**
   and swap its content from the forward "into-the-close" schema to an **EOD retrospective
   + next-session prep** schema. Net briefings/day stays 4. No extra scheduled Claude call
   beyond the shared news phase (below).
2. **Live news via the Anthropic web-search server tool** (not a news API/RSS): no new
   secret, no scraper — the model searches for the day's macro drivers + tomorrow's
   economic calendar inside the Claude call we already make. **Degrades to app-data-only**
   if web-search is unavailable/errors.
3. **All four briefings** gain **notable individual stock moves** + **macro drivers**
   (shared helpers). The intraday three stay forward-looking; the close is retrospective.
4. **Delivery**: the EOD recap renders as the infographic (reusing existing plumbing) AND
   reaches the phone. **Implementation note:** the phone push turned out to need **no new
   code** — the scheduled-briefing handler already pushes every slot's infographic as a PNG
   to Telegram + Discord, so the EOD briefing rides that path (see §E). The intraday
   briefings keep the same surface, just richer content.

## Architecture

### A. Shared inputs (new pure/assembly helpers, `services/options_svc/compute.py`)

- **`_notable_movers()`** — reads `cache:options:matrix` (every watchlist name carries
  `spot`/`day_pct`) + `cache:market:dashboard` tiles, returns the top ± movers by |Day %|
  (a small capped list `{symbol, day_pct, last}`), and flags any that also fired a UOA /
  crossover alert today (`cache:options:flow_alerts`). Pure over already-fetched caches;
  defensive → `[]`.
- **`_research_news(label, context)`** — **phase-1 Claude call** with the **web-search
  server tool** enabled (`tool_choice: auto`), prompting the model to find the day's
  macro drivers (Fed/CPI/jobs/earnings/geopolitics that moved the tape) and, for the EOD
  slot, tomorrow's scheduled economic calendar. Returns a short list of headline strings.
  Fully guarded: no API key / web-search unsupported / API error / **in-response search
  error** / empty result → `[]` (the briefing still renders app-data-only).

  **Verified spec** (researched against the claude-api reference): tool
  `{"type": "web_search_20260318", "name": "web_search", "max_uses": 3,
  "allowed_callers": ["direct"]}` — GA, **no beta header**; `allowed_callers` must be set
  explicitly because v20260209+ defaults it to `code_execution`. **$10/1,000 searches**
  (≈$0.04/day at 4 briefings). Two consequences worth calling out:
  - **Model support is not documented per model.** The official examples use
    `claude-opus-5`; support on our `_ANALYZE_MODEL` (`claude-sonnet-5`) is undocumented.
    So the news phase gets its **own `_NEWS_MODEL`** (a documented model), promoted to
    sonnet-5 only if a live probe proves it.
  - **A failed search returns HTTP 200**, not an exception: an error block
    (`web_search_tool_result` → `content.error_code`) followed by the model answering from
    memory. Publishing that text would put **fabricated headlines** in a briefing — worse
    than no news — so the parser detects the error block and returns `[]`.

  The **two-phase split is a hard API constraint, not a preference**: if the model calls web
  search and a client-side tool in the same parallel group the API returns
  `stop_reason: "tool_use"` and defers the search, so a forced `submit_*` render cannot also
  search.

`_notable_movers()` + `_research_news()` are consumed by **both** the intraday and EOD
paths. Movers are cheap (cache reads); the news phase is one extra Claude call per
briefing (see cost note).

### B. EOD retrospective assembly (new, `compute.py`)

`_eod_session_recap()` builds the app-data half of the EOD briefing, per index
($SPX/SPY/QQQ) from data already collected:

- **Day path** — session open→high→low→close + day %, from `gex_history.db` intraday spot
  series (first→last snapshot) and the live underlying; **where price closed vs the key
  levels** (held/broke the gamma flip, call wall, put wall).
- **Gamma-structure migration** — opening snapshot vs closing snapshot: flip level + call/
  put wall drift through the day (`gex_history_db.load_date_with_grid` / `latest_flip`).
- **Market context** — VIX change, breadth ($ADVN-$DECN), sector rotation / risk-on-off
  (`cache:market:dashboard`), sentiment score + **regime and any intraday regime
  transition** (`cache:sentiment:regime` + `regime_history`).
- **The day's flow events** — the UOA + crossover alerts that actually fired
  (`cache:options:flow_alerts`).
- **Next-session prep** (forward) — tomorrow's key levels ARE today's closing walls/flip
  (OI persists overnight), the next-session expected-move band (`_session_expected_move`),
  and the regime posture heading in. Macro **calendar for tomorrow** comes from
  `_research_news`.

### C. Claude calls + tool schemas

- **Intraday** (`gamma_analyze`, premarket/open/midday) becomes **two-phase**: phase-1
  `_research_news` → phase-2 the existing forced-`submit_analysis` render, with the news +
  movers threaded into the prompt. `submit_analysis` gains two **optional** fields:
  `macro_drivers` (array of short strings) + `movers` (array of `{symbol, move, note}`).
  The per-index playbook + what-if stay exactly as today.
- **EOD** (`eod_briefing`, close) is a **new** two-phase path with its **own** forced tool
  `submit_eod`: `regime`/`bias`/`headline`/`narrative` reframed as a **recap** ("what
  happened today & why"), per-index `recap` (open→close, levels held/broke) instead of
  `what_if`/`close_outlook`, a `next_session` block (key levels to watch, EM band,
  posture), plus the shared `macro_drivers` + `movers`. Same model (`_ANALYZE_MODEL`),
  same defensive parse-or-degrade discipline as `_parse_analysis`.

Both phases reuse the lazy `anthropic` import + local key resolution already in
`compute.py`. Any phase-2 failure degrades to a readable HTML page exactly like today.

### D. Infographic rendering (`compute.py`)

- A shared **"Notable movers"** strip + **"What's driving the tape"** (macro drivers)
  block are added to `analyze_infographic_html` (shown when the fields are present) — so
  the intraday briefings render them too.
- The EOD briefing gets its **own** layout (`eod_infographic_html`): a session-recap
  banner, per-index **recap** card (open→close, levels held/broke, day %), the movers +
  macro-drivers sections, and a **"Prepare for next session"** block (key levels, EM band,
  calendar, posture). Reuses `_ANALYZE_CSS`.

### E. Delivery — **the push is already free**

- **Infographic**: `eod_briefing` caches under the existing per-slot key
  `cache:options:gamma_analyze_close` and serves at `/options/analyze?slot=close`; the
  Gamma page "Auto briefings → Close" button is unchanged (now shows the EOD recap).
- **Push**: **no new code.** `handlers.run_scheduled_gamma_analyze` already renders EVERY
  slot's infographic to PNG and pushes it to Telegram + Discord via
  `push_notify.send_gamma_briefing` (with a text fallback if the headless render fails, a
  size guard, and a `gamma_briefing.webhook_url` override). The EOD result has the same
  `{"html", "analysis"}` shape, so it flows through that path unchanged. **This supersedes
  the originally-planned bespoke text push** (and the `discord.eod_briefing_webhook_url`
  key) — an infographic PNG is a better payload than a text line, and reusing the proven
  path removes a whole component.

### F. Scheduler & handler (`scheduler.py` / `handlers.py`)

- `_ANALYZE_SLOTS["close"]` moves **(14, 58) → (15, 15)**. Same once-per-trading-day latch
  + 20-min grace, trading-day/holiday gated. (15:15 is after the 15:10 EOD paper-P&L push
  and 15 min after the 15:00 CT cash close so quotes have settled.)
- `handlers.run_scheduled_gamma_analyze(bus, slot)` **branches**: `slot == "close"` →
  `eod_briefing` (+ the EOD push), all other slots → `gamma_analyze` (now movers +
  news-enriched). Persistence into the gamma-briefing history store is unchanged (the EOD
  payload persists like any other; its analysis dict has the recap fields).

## Cost note

Each briefing is now **2 Claude calls + 1 web search** (was 1 call). Briefings total
≈ **8 Claude calls + 4 web searches/day** (premarket/open/midday/close × 2). User-approved.
The news phase is fully guarded — a web-search outage silently drops to app-data-only, so a
provider hiccup degrades content, never breaks the briefing.

## Testing

- **Pure**: `_notable_movers` (top-± selection, alert cross-ref, empties), `_eod_session_recap`
  (level held/broke logic, open→close from gex rows, degrade on missing data),
  `eod_infographic_html` + the shared movers/macro sections, `eod_push_text`/`eod_push_embed`,
  the `submit_analysis`/`submit_eod` parse (defensive over adversarial tool input).
- **Two-phase call**: fake client — phase-1 news mocked (and mocked-absent → `[]`), phase-2
  structured render; the degrade paths (no key / web-search unsupported / no chains / no
  news) each render a valid page.
- **Scheduler**: the `close` slot time-change updates its existing tests; branch dispatch
  (`close` → EOD, others → gamma_analyze) covered.
- **Handler**: branch dispatch — `close` → `eod_briefing` cached under `gamma_analyze_close`
  (the existing PNG push then carries it), all other slots → `gamma_analyze`.
- **Live probe (mandatory, not optional).** The unit tests all use fake clients, so the live
  run is the only thing that answers: does `_NEWS_MODEL` actually support the tool, does a
  search actually *fire* (a `server_tool_use` block must appear — otherwise the "news" is the
  model's memory), and is the EOD prose genuinely retrospective? Three production bugs in this
  codebase's history (the `days=6` Schwab 400, the string `total_score`, the `CacheEnvelope`
  push that never fired) passed every unit test and were caught only here.

## Non-goals

- No 5th briefing slot (close is repurposed).
- No news API/RSS/secret (web-search server tool only).
- No push for the intraday briefings (infographic-only, as today).
- No buy/sell direction claims from flow (Schwab has no tape — unchanged).
