# webgui routes — full reference

The per-page detail that used to live inside the route table in [CLAUDE.md](../CLAUDE.md).
Moved out 2026-08-16: the table there is the index, this file is the detail. Text below is
verbatim from that table. **Durable invariants belong here; dated shipping narrative belongs
in [CHANGELOG.md](CHANGELOG.md).**

## `/desk`

**Desk — the app's HOME page (NEW 2026-08-18).** `/` redirects here (it pointed at
`/market` from 2026-08-16, and at the Market Scanner before that). Pinned at the
top of the rail in a **caption-less leading `NAV_SECTIONS` block** — the mirror of
the bottom-pinned `SYSTEM_RAIL` — so its breadcrumb is the bare leaf `Desk`. It
was alone there until 2026-09-17, when `/symbol` joined it: the two entry points.

⚠ **Each panel scrolls sideways INSIDE itself below ~1877px (private) / ~1809px
(public), rather than the document scrolling** (2026-09-09). The panel BODY is
the scroll container, never the card, so the heading stays put; each grid carries
a `min-width` DERIVED from its own `minmax()` track floors
(`pages/panel_scroll.py`), because a grid that keeps shrinking has nothing to
scroll. ⚠ **The pinned column is the one that NAMES the row, which is not always
the first**: dealer leads with SYMBOL and pins one cell, but Board leads with
SCORE, Flow with TIME and Positions with BOOK, so those three pin **two** —
pinning `:first-child` there would freeze a `PAPER` chip while the symbol scrolled
away. `_PIN_DEPTHS` is keyed by the grid string, the one thing every painter and
`_grid_head` already holds. The CSS is `shell.PANEL_SCROLL_CSS`, injected by BOTH
entrypoints so the published screen cannot diverge from the private one.

A single-screen aggregate of the highest glance-value element of each page, laid out
as the four questions a session opens with, in order: **top strip** (clock ·
Day/Week/Month **Sentiment** and **Trend** rings · **Bias** + **Signal** ·
Market Regime word · honest freshness indicator — a **$VIX quote until
2026-08-24**, when it gave way to the two band tiles and took `cache:options:header`
off the page's poll batch with it, that view having had no other reader here) → **Dealer Positioning** (`$SPX`/`SPY`/`QQQ`/`$NDX`:
spot + day %, gamma flip + signed distance, a positioned-div **structure bar**, call
and put walls, net GEX, and a pins-or-runs chip) → **Opportunity Board** (top 5 by
hotness, with ATM IV **and its direction**, and a setup tag) → **Live Flow Alerts**
(newest 5) → **Positions** (paper + driver merged, with `rescue_state` flags and an
`OPEN n · UNREALIZED $x · AT RISK m` header) → **MARKET SUMMARY** (full width,
below the grid — see its own subsection below). Panels sit in a **2×2 grid**
(`lg:grid-cols-2` — **not `xl`**, which is 1280px and silently collapses a 1265px
window to one column). Read-only + **click-through**: every row opens its owning page
already set to that symbol, reusing the one-shot `handoff.send_to_gamma` stash.
**Hover Bias, Signal or the Market Regime word** and a sentence explains it —
`regime_mix.REGIME_PICTURE` for the regime word (the same table `/sentiment`'s
dial hangs its hover from), `sentiment.BAND_WORD_PICTURE` for Bias/Signal.

Tier-1 reader of **eleven** views on **ONE batched 2 s `read_versions`** (cheap `:ver`
probes in a single pipelined round-trip; payloads deserialize only for views that
moved) — this page is open all day, so that matters. **No Highcharts at all**,
deliberately: nothing here is a time series, and the chart element collapses when it
mounts hidden, has no ResizeObserver, and loses in-place updates under the stock
module.

**Four invariants worth knowing before editing it:**
- **Every number is produced by the pure function its owning page uses**
  (`sentiment_arcs`/`trend_arcs`, `rings.ring_svg`, `flow.alert_rows`,
  `console_regime.regime_name`, `paper`'s DTE helper). The Desk composes; it never
  restates arithmetic. A drift guard test pins the regime word against
  `console_regime`'s. This exists because `/sentiment/sectors` and
  `/sentiment/rotation` already print opposite verdicts for exactly that reason.
- **One regime word, one source.** The pins/runs chip derives from `gex_regime`
  (spot vs flip) alone; net GEX is a magnitude beside it and may legitimately
  disagree, but never asserts a second regime word.
- **Walls are WITHHELD, not zeroed, when untrustworthy** — `stale`, or `net_gex`
  present-and-zero. Verified live 2026-08-18: index OI zeroes overnight, so `$SPX`
  published `put_wall=3000` against spot 7785 and `$NDX` `put_wall=14000` against
  spot 30046 while both carried `net_gex == 0.0` exactly. Both were suppressed;
  SPY/QQQ kept their real walls.
- **The structure bar is positioned divs with runtime `left-[{pct}%]`, not SVG** —
  a scaled `viewBox` would need `vector-effect: non-scaling-stroke`, which DOMPurify
  strips, smearing strokes while the server-side string stays perfectly correct.

**The Bull / Bear sector strip — two horizons on one chip (re-keyed 2026-09-05).**
A row of chips under the top strip, one per scored sector, reading the same
`cache:sentiment:bullbear` view `/sentiment/bullbear` does and clicking through to
it. Since 2026-09-05 a chip carries **both** horizons at once: the **fill colour
and the strip's left-to-right order are TODAY**, and the nightly cascade's
quarter-horizon quadrant survives as a **3px left border stripe**. That
combination is the point — a sector that has led all quarter and is falling today
is the most useful thing the chip can say — so nothing here may collapse the two
into one reading.
- **The horizon is decided ONCE per paint, by the calendar, never by the numbers**
  (`strip_is_live`). `_extract_change_pct` (schwab-proxy) falls through to a
  literal `0.0` when every percent field is missing or zero, and `0.0` is not
  `> 0`, so a "is any day move non-zero?" test would paint **all eleven sectors
  `falling_lagging` every pre-open and every weekend** — a maximally bearish
  reading of nothing. It asks `shared.market_calendar.regular_session_has_opened`
  (open through end of day, **not** `is_regular_hours`, which goes False at the
  cash close — the day's move does not stop being today's at 15:00 CT), AND that
  `benchmark_day_pct` reads through the strict `pages.fmt.num`, which is the only
  thing that catches a dead proxy mid-session. A NaN counts as absent; a
  **measured** `0.0` — a genuinely flat tape — stays live.
- **The off-session state is the structural horizon, not a blank.** Pre-open is
  when this strip is read to plan the session: every chip is still drawn, on the
  quarter, and the caption says so. ⚠ It is therefore never safe to read a
  pre-open colour as a claim about today, which is exactly what the caption and
  the headline's horizon word exist to prevent.
- **One classifier, two horizons.** `bullbear.row_day_axes(row)` returns
  `(day_pct, day_excess)` from the row's **top level** — `raw` is the cascade's
  own block, which `merge_live` copies beside rather than into — and feeds the
  unchanged `quadrant()`. There is deliberately **no fallback to `raw`**: a row
  with no live fields has no intraday reading, and painting the quarter's reading
  in today's colours is the outcome the feature exists to prevent. Because there
  is one rule, a strip/map disagreement is always a difference of *horizon*.
  ⚠ **Accepted residual, per row rather than per strip:** once the strip is live,
  a symbol the proxy *returns* with unusable percent fields still reads a literal
  `0.0`, and `0.0` is not `> 0` — so that one chip paints falling. The calendar
  switch bounds the damage to individual rows mid-session; what it makes
  impossible is the whole strip reading bearish off no data. A zero here is
  therefore no proof of a flat tape, the same trap `bullbear.signed_pct` states
  for the day-move cell.
- **Order is `bullbear.by_day_move`, with bucket hysteresis.** The move is
  quantised into `DAY_SORT_MARGIN_PCT`-wide buckets and the row's seat in the
  previous paint breaks ties, so order holds *inside* a bucket and a chip changes
  seats only on crossing a boundary — a pure function of `(rows, previous)`, no
  state machine and no clock. The seat list lives in page state and is fed back
  each paint; without it the margin buys nothing. ⚠ Two accepted residuals, both
  pinned by test: a pair straddling a bucket boundary **still swaps** on every
  repaint (bounded to adjacent seats, since such a pair is within one margin),
  and `margin` must stay a parameter — the constant binds as a default at `def`
  time, so patching the module attribute never reaches the call. `math.floor`,
  never `int`: truncation would double the width of the bucket spanning flat.
- **⚠ The order deliberately diverges from `/sentiment/bullbear`'s `by_strength`,
  and two labels are what make that legal.** `bullbear_caption` states what the
  strip sorted by (and names the stripe, since a colour on an edge is not
  self-explanatory) and `bullbear_headline` appends the horizon word — "… today"
  against "… on the quarter" — to the map's own count sentence. Remove either and
  two screens rank one payload differently with neither admitting it, which is
  the `/sentiment/sectors`-vs-`/sentiment/rotation` failure one screen earlier.
  An empty headline takes no horizon word.
- **`border-l-[3px]` lives on the chip FRAME at both horizons**, so the strip does
  not reflow 3px sideways when the bell flips it; off-session the left border just
  takes the quadrant's colour. The stripe class is only added when live, together
  with its tooltip — `"On the quarter: …"`, in `bullbear.quadrant_label`'s words,
  hung on the whole chip because 3px is too small a hover target to be the sole
  carrier of a reading. ⚠ The stripe wins the left edge on **Tailwind v4's
  canonical property order** (`border-left-color` follows `border-color`), not on
  DOM class order; `pages/options/leg_editor.py`'s accents depend on the same
  thing.
- **One clock per paint — now shared with the MARKET SUMMARY frame below.**
  `_paint_bullbear` takes a single `now` for the chips and the headline, and
  `_paint_summary` (→ `summary_facts`) is handed the SAME `now` rather than
  taking a fresh `datetime.now()` of its own. Two clocks would let one region
  say "on the quarter" while the other has already flipped to "on today's
  moves", at the opening bell — the one-word ambiguity `/sentiment/bullbear`
  exists to remove, reintroduced between two regions of one page.
  `bullbear_distribution` (the Bull/Bear chip's hover) does not mint a clock
  either: it renders the `counts`/`live` pair `summary_facts` already derived,
  rather than deciding its own horizon. `test_one_paint_decides_the_horizon_once`
  pins the call sites, by identity rather than equality — two `now()` calls
  microseconds apart compare unequal only sometimes, and a guard that fails
  only sometimes is not a guard.
- **Tier 2 supplies the second axis for free.** `bullbear_symbols` yields the
  benchmark, so SPY rides the ONE batched `/quotes` call the tree already makes;
  `merge_live` attaches `day_excess = day_pct - benchmark`, and the payload gains
  `benchmark_day_pct`. `day_excess` is `None`, never `0.0`, whenever either side
  is missing — `None` means the proxy omitted the symbol, never "unchanged". Both
  reads go through the single `_quoted_day_pct`, which rejects `bool` and numeric
  strings on top of `_as_finite`'s non-finites.

Design: [`2026-09-05-desk-bullbear-intraday-design.md`](plans/2026-09-05-desk-bullbear-intraday-design.md).

**Spoken arrival alerts + the neon glow (2026-08-21).** A new **flow alert** or a
**newly-opened position** is announced out loud — ticker spelled squawk-style, then
the CONTRACT where the alert names one ("N D X. Unusual activity, 0-D T E 7 15
Put." · "S P Y. New position, put credit spread. 2 07. point 5, 2 05, 8 - 31, entry
56 cent credit.") — and its row glows **cyan for 10 seconds**. A position whose
**flag** moves (OK → AT RISK → RESCUE) glows **amber and stays silent**: it was
already in the book, and the FLAG column already prints the new word. All **four**
flow kinds speak, `big_delta` included — that deliberately
diverges from `alerts.py`'s quiet-live exclusion, because the exclusion exists to
stop an *information-free chime* at that frequency and an announcement naming the
ticker and the contract is not one.
- **Per-section switches + the board voice (2026-09-18).** `voice_board` /
  `voice_flow` / `voice_positions` sit under `voice_enabled` (Settings shows them
  greyed while it is off). `desk.detect_utterances` runs every section's fold
  regardless and drops only the SENTENCE for a silenced one — skipping the fold
  would leave its seen-set stale, and re-enabling it would announce the backlog. A
  symbol joining the top `BOARD_ROWS_N` speaks (`fold_board_arrivals`,
  `voice.board_phrase`); one that left within `BOARD_REENTRY_QUIET_SEC` (30 min)
  glows silently, which also covers the whole board reappearing after an empty
  matrix. Board glow keys are namespaced `board:<SYMBOL>` since the glow map is
  shared. The flow-clip prewarm is gated on `voice_flow` too.
- **Two phrase forms, chosen by what the row CARRIES, not by the alert kind.**
  `uoa`/`big_delta` carry a strike + expiry and take the contract form (the word
  "alert" dropped, the side moved after the strike); `crossover`/`gamma_flip` carry
  none and keep the original short form verbatim. Deciding on the PARSED values
  makes the degrade path and the contract path one line of code, so they cannot
  drift: an unreadable strike falls back to the short form — **shorter, never
  half**, since a sentence with a hole in it is worse than a terse one and silence
  is worse than both. `voice.say_number`/`say_expiry`/`say_entry`/`say_strikes` are
  the vocabulary; `say_number`'s rule ("2 05", "4 5 hundred", "2 07. point 5") was
  settled by a listening test and its cases are pinned. `say_entry` lets the SIGN
  pick the word because the paper book stores a debit as a **negative**
  `entry_credit`.
- **`flow.alert_rows` gained `strike`/`expiry`/`dte`** (additive; no column declares
  them) so the Desk composes off the same row the Flow Alerts page draws rather than
  becoming a second reader of the raw payload.
- **The prewarm SHRANK to the contract-less kinds** — `voice.FLOW_CAUSES` is derived
  as `_ALL_CAUSES` minus `CONTRACT_KINDS`, 8 pairs → 4. A uoa phrase's space is the
  option chain, so warming it synthesized sentences no live alert can produce. A burst says the **newest only, plus a count**
("…Plus 5 more."), **one utterance per panel per paint**, so a tick is bounded at two
clips; detection runs over the **full** alert list, not the five rows drawn, or a
burst's arrivals would announce themselves later when the list shortened. **First
paint seeds all three sets silently** — navigating here must not read out the day's
backlog or light every row.
- **The glow RESUMES across repaints, it does not restart** — `_paint_positions`
  rebuilds every row on each re-price, and **a rebuilt element restarts its CSS
  animation from zero**, so the naive version glows forever. The start time lives in
  page state keyed by row id and each row wears one of ten static classes
  `desk-neon-0…9`, each carrying a whole-second **negative `animation-delay`**. Ten
  fixed classes rather than a computed delay: the finite-set rule. ⚠ `GLOW_SEC` and
  `GLOW_STEPS` must move together — `desk-neon-10` has no rule behind it, and the
  failure mode is a silent restart.
- **Clips are synthesized server-side by `webgui/voice.py` (`edge-tts`)** and cached
  permanently as `sha1(voice|rate|text)` under `webgui/data/voice/` (gitignored),
  served from the **`/voice`** static mount. ~0.9–2.4 s on a miss, ~110 µs on a hit,
  ~22–28 KB a clip; a background prewarm warms the flow phrases at startup. Nothing
  on `voice`'s public surface raises — a dead endpoint costs the sentence and
  nothing else, and the row still glows.
- **The page carries its OWN `<audio id="desk-voice">`**, not `main.py`'s shared
  `alert-audio`, so a scanner chime (fired by the app-wide watcher on every page)
  cannot cut an announcement off mid-sentence.
- ⚠ **Browser autoplay refusal is completely silent** — `play()` rejects with
  nothing in any log — so the header carries an **ENABLE SPOKEN ALERTS** chip, hidden
  until a block is actually reported; the click that dismisses it *is* the unlocking
  gesture. A blocked attempt **clears** the queue rather than holding it.
- **Gated by `voice_enabled` and the EXISTING `alert_market_hours_only`** — there is
  deliberately no second market-hours switch to drift out of step with the chime's.

Design: [`2026-08-18-desk-home-dashboard-design.md`](plans/2026-08-18-desk-home-dashboard-design.md)
· [`2026-08-21-desk-voice-alerts-design.md`](plans/2026-08-21-desk-voice-alerts-design.md).

**The MARKET SUMMARY frame + the Regime popup (2026-09-10; the summary quotes the market report since 2026-09-16).** Full width, below
the four panels; the public live Desk renders it too.

- **The latest market report's highlights (since 2026-09-16).** Up to five
  points — the report's own section headlines, in report order — over the
  report's provenance ("Market close report · 14 Sep · 16:20 CT") and a **Read
  the full report** link (`report_url`, `https://<SITE_HOST>/report.html`, drawn
  only when it is https). `market_svc/report_summary.py` parses
  `deploy/site/reports/latest.html` (+ `latest.txt`) whenever its stamp changes
  — a `stat` per poll — and publishes `cache:market:summary` (`MarketSummary`:
  `headline`, `highlights`, `slot`, `slot_label`, `report_date`, `as_of`,
  `report_url`). **No Claude call**: the change-driven Claude sentence
  (`summary_facts`/`generate_summary`, the fingerprint gate, the 30/day cap) was
  retired the same day. A report that does not parse publishes nothing, so the
  last good highlights stay. ⚠ The markup is a contract with the report
  renderer outside this repo: `div.slotchip`, `h1`, one `h2` per section.
- **A `summary` region on the Desk's existing batched poll** —
  `cache:market:summary` is in `VIEWS`. `summary_facts(summary_view,
  composite_view, history_view, regime_view, bullbear_view, now)` builds
  everything the frame draws: `points`, `source`, `url` and the six chips (each
  reusing the strip's own derivation, so the frame and the strip can never name
  one reading two ways). The point rows are a fixed set of five filled in place.
- **Six live chips** — SENTIMENT, TREND, BIAS, SIGNAL, REGIME, BULL/BEAR — read
  off the views the page already polls. Each carries the same hover its
  counterpart uses elsewhere on the page, plus **Sentiment**
  (`desk.SENTIMENT_TIP`) and **Bull/Bear** (`bullbear_distribution(counts,
  live)`, the four-quadrant distribution and its horizon).
- **Empty state** (`desk.SUMMARY_EMPTY`) — "No market report published yet." An
  unpublished reading behind a live chip shows a dash, never "Neutral".
- **The Regime popup.** Hovering the Market Regime word — here and on
  `/sentiment`'s regime dial — shows one sentence per word from
  `regime_mix.REGIME_PICTURE`, keyed by the DISPLAYED word (11 entries: Balanced,
  Trending, Rallying, Firming, Retreating, Softening, Breakout, Breakdown,
  Whipsaw, Stressed, Unclear). `shared/tests/test_cross_tier_mirrors.py` pins that
  every word the sentiment service can print has one.

Design: [`2026-09-10-desk-market-summary-design.md`](plans/2026-09-10-desk-market-summary-design.md).

## `/symbol`

**Symbol Dossier (NEW 2026-09-17)** — one screen per ticker, pinned beside the Desk
in the caption-less leading rail block (icon `manage_search`; bare one-crumb
breadcrumb). Desk answers *what is happening*, Symbol *tell me about X*. Built as an
**index over the pages that own each fact**, never a replacement: every band ends
in a link out, and every number comes from the builder its owning page uses.
`webgui/pages/symbol.py` (widgets + band assembly) over the pure
`pages/symbol_facts.py`; the wall/flip bar is `pages/structure.py`, shared with the
Desk. Design + plan: [`2026-09-17-symbol-dossier-{design,plan}.md`](plans/2026-09-17-symbol-dossier-design.md).

**The route takes `?symbol=`** (`symbol_page(symbol=None)`), so a dossier is
linkable and bookmarkable. ⚠ That parameter is settable by anyone who reaches the
app, and it names a view, a command and a Redis key — so it passes
`shared.symbols.clean_symbol` (`[A-Z$][A-Z0-9$.]{0,7}` after upper/strip) first, the
SAME allow-list the service applies; anything refused renders *"X is not a ticker
symbol."* and enqueues nothing.

**Bands**, one question each: **Structure** (put wall → call wall bar with spot and
flip, flip side + distance, net GEX, pins-or-runs) · **Volatility** (Vol Rank bar,
IV vs HV with the scorer's *high ≥ 1.2× / low ≤ 0.9× / mid* words, ATM IV + direction,
1σ expected move for a day and a week) · **Context** (regime word, sector / industry
+ Bull/Bear quadrant and rank, earnings) · **Today** (signals with age + score trend +
sparkline, and flow alerts) · **Your position** (all four books — account, ledger,
driver, captured — with the rescue flag only where the book carries one). No
Highcharts, the Desk's call.

**Reads** 11 shared views (`options:matrix`, `:scan_funnel`, `:scan_day` via
`read_gated`, `:gex_status`, `:flow_alerts`, the four books, `sentiment:regime`,
`:bullbear`) + its own `options:dossier:<SYMBOL>` on ONE batched 2 s
`read_versions`; `REGION_VIEWS` repaints only the bands whose views moved. The first
read runs off the event loop (measured 37.6 ms against a 3.98 MB day union).

**Coverage is per FACT, not per symbol** (`symbol_coverage`): **scanned** (a matrix
row + a funnel account — everything cached), **collected** (a matrix row only —
`$VIX`, the sector ETFs — structure cached, Vol Rank / IV vs HV / earnings fetched),
**unknown** (nothing cached). A funnel account with no matrix row is a cold matrix
and reads unknown.

**The fetch rule — the one that costs money.** An on-demand `dossier` command on
`cmd:options` (4–5 Schwab calls) is enqueued ONLY on navigation or an explicit
Refresh, for a symbol that is not scanned, with a live options feed
(`should_enqueue`); **the poll timer can never reach the enqueue**, pinned at
source level. Navigation reuses a cached dossier inside its **15-min TTL** unless it
recorded `fetch_failed`. The service adds a **60-s dedup**: a dossier written under
a minute ago (and not `fetch_failed`) is not re-fetched, and the page answers that
Refresh as already current. **Cache wins** (`merge_facts`): the fetch only fills
gaps, since a one-minute matrix row beats a point-in-time snapshot; the per-fact
`source` map is what lets the page say "fetched HH:MM" only beside facts that were.

**Find trades** (header, beside Refresh) hands the symbol to the Strategy Finder
through the existing one-shot `handoff.send_to_swing` stash — the Trade Plan's path
— and the Finder seeds its input and runs `swing_scan` at once. `finder_allowed`
draws it only once the name has a **real quote** (scanned, collected, or a dossier
with `error is None` and a finite spot): hidden while a look-up is pending and for
`no_quote` / `fetch_failed`, re-checked at click time, and gated on
`shell.can_navigate`. ⚠ Unlike the Dealer Positioning link it starts nothing
recurring — a Finder scan is one-shot, and a chain listing more than 30 expirations
asks before it fetches — so the gate is about not sending anyone to a scan that
cannot work, not about cost.

**Header chip**: `SCANNED HH:MM` (the funnel's scan time, so post-close Vol Rank
reads its age) · `COLLECTED` / `COLLECTED · FETCHED HH:MM` · `FETCHED HH:MM` ·
`FETCHING` · **`QUEUED`** (the 30 s `LOAD_TIMEOUT_SEC` backstop fired with no answer —
`cmd:options` has one consumer, so a look-up can sit behind a 26–40 s whole-chain
Finder scan; the page says it will appear when the service answers and does NOT
invite Refresh, which would queue a second paid fetch) · `NOT FOUND` (`no_quote` —
"check the symbol") · `FETCH FAILED` (an outage — never "check the symbol") ·
`NO DATA`.

**Walls are gated on where they came from.** A CACHED wall needs a live collector
(`gex_freshness` → live / stopped / unknown, the Desk's rule, each worded apart); a
FETCHED wall is shown with *walls fetched HH:MM*, because the collector never drew
it. Both are withheld when net GEX reads exactly `0.0` — the after-hours all-zero
grid, whose walls are an argmax tie-break — and the service refuses to publish walls
for such a grid too (`dossier.zero_grid_walls_ok`, mirroring
`structure.walls_trustworthy`). The bar needs BOTH walls, so a mixed-source or
half-withheld pair draws no bar.

**→ Dealer Positioning is drawn only for a scanned or collected symbol**
(`gamma_link_allowed`). The Gamma page points the shared sticky
`cache:options:gamma` slot at whatever it is handed and the service refreshes it
every GEX tick; for an uncollected symbol that is a fresh chain fetch a minute, all
session.

**Empty states say different things**: a cold feed prints `copy.WAITING_OPTIONS` /
`WAITING_SENTIMENT`; a quiet name prints its own line (*No signals for MU today.*,
*No open position in MU.*); a previous session's day union prints the scanner's
`day_note`, never live rows. Earnings keeps three values — `not_listed` reads *not
covered by the calendar*, never *none scheduled*.

**Private only**: it enqueues, which the public process refuses, so it is NOT in
`live_screens.SCREENS`. Links in: the Opportunity Board's symbol cell.

## Trade detail panel — Expected Move on captured signals (2026-08-25)

The panel gates its Expected Move expansion on the signal carrying an
`expected_moves` dict. Scan signals carry it for 100% of rows; **captured signals
carried it for none**, so the expansion never rendered on `/options/captured` —
reported after the expansion reorder, but long-standing rather than caused by it.

Captured rows had a price but **no IV at all**. ⚠ `entry_iv_rank` is a
PERCENTILE, not an implied volatility — feeding 52.8 in as a vol would print a
confident, wrong move — so the IV now comes from `signal_repricer.atm_iv(chain)`,
taken off the chain the reprice cycle **already fetches**. No extra Schwab call.

Three things worth knowing:

- It is the **ATM** IV (strike nearest spot), deliberately not the position's
  short leg: the short leg is OTM, so its IV carries skew and would
  systematically overstate the move — upward for a put spread, which is most of
  this book.
- `atm_iv` reads **both** chain shapes. Measured against a live Schwab chain on
  2026-08-25, `underlying` came back **null** while the top-level
  `underlyingPrice` carried the spot — reading only the nested `underlying.last`
  refused a perfectly good IV for want of a price.
- **It populates during RTH only.** The whole captured reprice path needs live
  bid/ask (`_leg_bid_ask` returns None on a zero bid or ask), so off-hours there
  is no mark at all — the same reason the P&L column reads an em dash. Not a
  regression; the expansion simply follows its data.

One IV is applied to all three horizons, which is a term-structure
approximation — but it is the identical one `iv_analysis.calc_expected_moves`
already makes for scan signals, so the two pages stay comparable.

## Trade detail panel — score bar + expansion order (2026-08-25)

The header's score is an **SVG bar** (`svg.score_bar_svg`), not the Highcharts
angular speedometer it replaced: a dark track, a gradient fill running dark → the
value's own `value_color`, a tick at the fill's leading edge, and the number
beside it. The red/amber/green semantics are unchanged, so a 20 cannot look as
healthy as an 80. **A missing score draws a bare track and an em dash** — the
gauge could only render absence as a filled-to-zero face, so `gauge_metric` now
returns `value: None` instead of `0`.

⚠ **The panel no longer provides a Highcharts ESM anchor.** The gauge was the
only chart on all four pages that mount it (scanner / captured / paper / swing),
so none of them loads Highcharts now. A `ui.highchart` created after first render
on a page that had none dies with "Failed to resolve module specifier
nicegui-highcharts" — so any of those pages gaining a chart must bring its own.
Pinned by an AST guard in `test_options_detail.py` (AST, not substring: the
module's own comment explains the absence and a substring scan reads that as a
violation).

The four EXPLORE expansions are ordered **Expected Move · Greeks · Implied
volatility · Score factors** (operator preference). Expected Move renders only
when the signal carries an `expected_moves` dict — scanner swing/directional
signals do, captured signals do not, which pre-dates the reorder.

## Trade detail panel — the two expected-value rows (2026-08-25)

Shared by every signal table, so it is documented once here rather than per route.

The **ECONOMICS** block carries two figures about expected value, answering
different questions. **Needs** is structural — the win rate this trade's own price
demands, `max_loss/(credit+max_loss)` — rendered directly under `Probability` so
the margin needs no arithmetic. **Signals like this** is the recommendation:
realized R per trade for this signal's family and score band, read from
`cache:options:calibration` (published nightly by `options_svc`, version-gated
page-side since it moves once a day).

⚠ The **priced** EV is deliberately absent, and `expected_pnl_10` was REMOVED from
the "Score factors" expander on 2026-08-25. Both its terms come from the option's
own price, so it is ~0 by construction — and where it is large it is measuring a
broken mark: the top three live signals by priced EV that day carried relative
bid-ask spreads of 225%, 239% and 395%. It survives where it is correct, as the
width-selection gate in `scanner_engine.select_best_width`.

Either row is **omitted entirely** — not dashed, not flagged — when it cannot be
computed honestly. A Strategy Finder signal has no `credit` (so no breakeven) and
carries `max_profit`, a TAIL outcome that would print **+2137R** for a long put if
used as `b`; note its `unbounded` flag reads False for every one of the worst
offenders, so that flag is not the guard. The calibrated row is additionally
withheld when its bucket's day-clustered t is inside ±2. Builders:
`pages/options/ev.py` (PURE, unit-tested); design:
[`plans/2026-08-25-ev-in-trade-detail-design.md`](plans/2026-08-25-ev-in-trade-detail-design.md).

## Trade detail panel — the Go / No-Go checklist (2026-09-15)

The panel opens with the checklist (`pages/options/checks.py`, PURE) above the
contract cards, on **candidate pages only**: `update(signal, candidate=…, ctx=…)`
takes a candidate from `detail.checklist_candidate(signal, allow_paper)` — the RAW
signal the table stamped its chip from plus that row's `_allow_paper`, exactly what
`checks_table.stamp_checks` hands `checks_feed.checks_for`, so the panel and the
chip judge the same thing. Not the `detail_signal` copy the panel displays, which
rescales per-contract dollars. A page showing a position already HELD passes none
and no checklist renders — whether to open a trade is the wrong question about one
you hold. The Market Scanner and the Strategy Finder are the two callers.

Nine checks in `checks.ORDER` — **book · earnings · vol · cost · em · wall ·
gamma · direction · record** — each a label and a sentence, coloured by tone
(`pos` emerald / `warn` amber / `neg` rose / `muted` grey), under the `summary`
headline capitalised page-side (the chip's own `unchecked` is lower case). A check
that does not apply is **omitted**, never rendered as passing, and **only `book`
can be `neg`**.

Three lines the box shows instead of a verdict, and each is a deliberate refusal
to guess: **`CHECKING_TEXT` "Checking…"** while the context is read (the page
hands over the `ctx` it last stamped its rows against; without one the panel reads
its own through `run.io_bound`, since `checks_feed.read_context` holds per-view
locks across a Redis round-trip); **`NO_CHECKS_TEXT`** when nothing applies; and
the **gone** line when the page no longer lists the row — `set_candidate_source`
registers the page's `lookup(id)`, and a `None` answer paints
`GONE_SCAN_TEXT` / `GONE_FINDER_TEXT` (“This signal is no longer in today's scan” /
“This trade is no longer in the scan's results”) plus the shared tail *— the
details below are as it last read*, which names the WHOLE panel because the
contract, score bar and economics around it still describe the departed row. Gone
is **terminal** for that selection: the panel stops judging it even if the id comes
back, and the way back is clicking a row again (which is also the only thing that
refreshes the stale cards below). `refresh_checks(ctx)` repaints the checklist
ALONE, so an expansion the reader opened stays open, and an identical view leaves
the elements untouched. A lookup that raises is logged and read as gone rather than
abandoning the rest of the paint it runs inside.

⚠ **`ev.calibrated_facts` now returns `None` for a row carrying `fit_score`**, so
the *Track record* check and the panel's *Signals like this* row both disappear on
Strategy Finder rows: `strategy_scoring` overwrites `composite_score` with its
Fit+Quality score while the row keeps `trade_type="SWING"`, so the scanner's
calibration buckets — built on the premium composite — were answering a different
scale.

## `/options/scanner`

Options · Market Scanner (0-4 / 5-15 DTE, two-pane + detail panel; **THREE folder-style SUBTABS since 2026-07-16 — 0-DTE / Swing / Directional**. **Directional** renders the engine's `signals_directional` (single-leg LONG_CALL/LONG_PUT/SHORT_CALL/SHORT_PUT) via the SHARED `strategy_table` builders, scored on **Fit+Quality** (never beside a premium composite — see the Last-updated entry); naked shorts show `Max L = ∞` + an undefined-risk badge and no Paper button. **Since 2026-08-06 the ENGINE only emits non-Weak candidates scoring ≥ 50** (`scanner_engine.SINGLE_LEG_MIN_SCORE` / `SINGLE_LEG_EXCLUDED_GRADES`, cut before the per-symbol cap) — an empty Directional tab now means "nothing cleared the bar", not a failure, and long CALLS largely vanish because the documented unbounded-profit R:R artifact scores them ~14 points below long puts. **The tables read `cache:options:scan_day`** (the day union) not `cache:options:scan`, so the day's signals persist to EOD with dropped-out ones **dimmed + frozen + "Dropped HH:MM"** and **no Paper button** (frozen price + verbatim `entry_credit` = a fictional entry); the render is **gated on the envelope's CT date** and surfaces a `truncated` notice. The status bar still reads the LIVE key (the day envelope carries no timestamp/errors) and says "N live signals" so it can't be read as the day count. **"New" = unseen since you last VIEWED the page** (acknowledged only on initial paint), keyed on the engine's unique `id` — this fixed a real bug where the key collapsed to `SPY|PCS|None|None|07/17`; **a webgui restart re-marks everything New** (page-side state, deliberate). ⚠ the nav badge/chime still count credit spreads ONLY — a Fit+Quality score isn't commensurable with the premium composite the min-score alert threshold gates on;  under the main tab strip** (2026-07-11, `shell.subtab_slot()` + `.compact-subtabs`; amber/blue tab text kept) with **live signal counts** (`checks_table.filtered_tab_label`); **Run scan is right-aligned flush with the table** (`.scan-panels` drops the q-tab-panel padding); a new qualifying signal pops an **in-app toast** (`fiber_new`, blue-8 — matching the row "new" badge) alongside the chime/desktop notification; **Run scan** is the app's solid primary button (`color=None` + `.scan-btn`); the per-row **Send to Calculator** now transfers correctly — `_prefill` stashes `pending_legs` + `load_symbol()` so legs apply AFTER the chain loads, instead of being wiped by strike-coercion against an empty chain (see [[calculator-leg-transfer-needs-chain-first]]))

**Paper button → Ledger caps (2026-09-15).** Send to Paper trade enqueues `paper_create`, which opens into the Paper Ledger only if the trade clears every `shared.book_caps` rung against the Ledger's own open trades ($750 per trade, plus the Account's symbol / sector / expiry / 20%-deployment caps); a refusal writes nothing. The page watches **`options:paper_create`** (`handoff.watch_paper_results`, 1 s poll, versions compared with `!=` because the 600 s TTL resets `:ver`) and toasts the answer — *Paper ledger: opened …*, or *Paper ledger: not opened — <reason>.* with *Up to N contracts fit.* when a smaller size would clear. **The dialog previews the decision (2026-09-15, Phase 3).** `handoff.send_to_paper` reads **`options:ledger_caps`** once on open and renders `handoff.paper_dialog_view` over the pure `book_fit.preview` — the same `shared.book_caps` rungs, bucketed with `sector_bucket` over the published sector table and priced with `booked_risk` over the row's `ledger_risk_basis` stamp: *Risk $X per contract*, then one line per rung (green fits / red breaks / muted not checked). A breach, a quantity that is not a whole number ≥ 1, or one above 100 disables Create (*Up to N contracts fit.* / *No quantity fits the paper ledger's limits right now.* / *The dialog opens at most 100 contracts in one trade.*); the quantity box's max is the largest fitting quantity (1 when none fits). A dialog that cannot preview (no view, no stamp) says so and leaves Create enabled — the service still checks. Create latches against a double click; an unreachable bus says *Could not reach the options service — the trade was not sent.* and re-enables. The send toast is *Sent N contracts — the paper ledger answers in a moment.*

**Checks column and “Only clear” (2026-09-15, Phase 4).** Every row carries the Go / No-Go checklist's one-chip verdict in a **Checks** column on all three tables — the short form in the cell (`Blocked` / `2 cautions` / `Clear · 7 of 9` / `Partly checked` / `unchecked`, an em dash before the page's first context read), and `_checks_tip` on hover (`checks_table.CHECKS_SLOT` prefers it and falls back to the cell value). ⚠ The hover is NOT always the verdict: a **caution** chip's own words repeat the cell, so `verdict` returns `reasons` on that branch — the warn lines' own text joined with ` · `, capped at `checks._MAX_REASONS` (3) plus `…` — and `stamp_checks` writes that as the tip; every other state keeps the verdict text, which already says more than the chip. The column is **not sortable** (`_checks_col`, `sortable=False`, matching the Finder's): its words sort alphabetically, which is not an ordering of how clear a trade is. `stamp_checks` runs immediately after `stamp_stale` inside `_build_populate`, and that ORDER IS LOAD-BEARING: the Paper book line reads the `_allow_paper` gate `stamp_stale` settles, so a stale row's book line closes with it. The **Only clear** switch sits beside Run scan (tooltip *“Hide rows with a block, a caution, a feed that hasn't loaded, or a paper book fit that couldn't be checked”*), filters the stored rows with NO bus read, and drives `filtered_tab_label` (*Swing (3 of 40)* filtered, *Swing (40)* not, the bare name before today's scan exists) plus `only_clear_empty_label`, which distinguishes three empties: nothing stamped yet (*The checks haven't loaded yet — turn off Only clear to see all N.*), every hidden row only partly checked (*Every row is only partly checked — a feed the checks read hasn't loaded. Turn off Only clear to see all N.*) and the filter doing its job (*No row is fully clear — N hidden by Only clear.*). It filters on `_checks_clear`, not the chip: the chip reads Clear while the Paper book line is grey, and a row whose fit was never checked must not pass a filter that promises it was — an unstamped row is hidden, so the filter fails closed. **Refresh (`repaint_action`):** a scan view (`options:scan_day` / `options:scan`) moving rebuilds everything; one of `checks_feed.REFRESH_VIEWS` (`options:ledger_caps`, `sentiment:regime`, `options:calibration`) moving re-stamps the painted rows off the loop through `checks_table.read_and_restamp_tables` — ONE context read for all three tables, so two tables can never be stamped against different contexts, onto SHALLOW COPIES, because the event loop may be filtering the same dicts for the switch; and `options:matrix`, which moves every minute, is NOT a trigger — the walls / flip / trend it feeds re-stamp on the fixed `checks_feed.TABLE_REFRESH_SEC` **5-minute** timer, and then only when one cheap `:ver` probe says the board moved, so it costs nothing off-hours. **A re-stamp never re-scans** (a scan is a paid Schwab fetch). The detail panel is handed the SAME context the rows were stamped against (`checks_ctx`), and `checklist_candidate_for` builds its candidate from the SERVER's painted row — never the browser's copy — so the panel's Paper gate matches the chip's.

**"Why no trade?" — the scan funnel panel (2026-09-15, Part 3).** A flat `help_outline` button sits **left of Run scan** (`FUNNEL_TITLE`, `no-caps flat dense` in `MUTED` — it explains the tables rather than changing them, so it must not read as the page's action) and opens a dialog over **`cache:options:scan_funnel`**, the per-symbol account `scanner_engine.run_full_scan` keeps of where each scan window stopped. **The read happens on OPEN, never at page build** — nobody reading the tables has asked for the funnel — and goes through `run.io_bound` (`_read_funnel`) like every other bus read here; a `fetching` latch means a second open while one is in flight re-shows the dialog and starts no second read. It reads **two** views: the funnel, and `options:scan` for its `timestamp` **alone**, which is what lets `funnel_view.stale_note` print *From an earlier scan.* on every card (two stamps are needed to say one is older than the other; with either missing it correctly says nothing). Picking a symbol repaints from the STORED payload and re-reads nothing; **closing and reopening re-reads**. Until the read lands the dialog shows `FUNNEL_LOADING` (*Reading the last scan…*) and **no cards** — the previous open's verdict under a fresh dialog reads as this scan's answer. The dialog is built at the PAGE's own level, never inside a container a repaint clears (the `swing.py` precedent: a `ui.dialog` deletes itself with the slot it was built from).

The dialog holds three things. **Chips** (`funnel_chips`), one per window in `FUNNEL_BUCKETS` order (`0DTE` / `SWING` / `DIRECTIONAL`, the engine's own spellings; `funnel_view.BUCKET_LABELS` carries the reader's *0-DTE* / *Swing* / *Directional*), each reading *0-DTE · 1 of 4 produced nothing* — `len(funnel_view.empty_symbols(payload, bucket))` of the symbols the funnel accounts for, `BADGE_WARN` when non-zero and `BADGE_MUTED` at zero. ⚠ `funnel_chips` returns `[]` for a payload with no symbols: `empty_symbols` answers `[]` both for "nothing was empty" and for "there is nothing to read", and *0 of 0 produced nothing* off a cold view is exactly the zero this app must never print — the waiting line (`copy.WAITING_OPTIONS`) carries that case instead, because it is the one thing the per-symbol cards cannot say. ⚠ `empty_symbols` also **leaves out** a symbol whose bucket is absent or carries no `emitted` count: it has not emitted zero, it has said nothing. A symbol the scan STOPPED on is included — it really did produce no signal, and its card says why. **A symbol picker** (`with_input`) over `funnel_symbols(payload)`, seeded by `funnel_seed` with the reader's own symbol while the new payload still carries it, else the first; hidden when there are none. **Three cards** (`funnel_cards` → `funnel_view.bucket_card`), each a headline, the optional stale note, and one row per stage — label left, survivors remaining right — with the **binding stage** (`stage_class` → `TXT_WARN`) picked out and the rest `MUTED`.

**The binding stage IS the answer**: the first stage whose remaining count is 0, and the headline is that stage's own sentence naming the count that entered it (*38 short strikes were priced, and every one sat past the short-delta ceiling.*). When nothing binds the headline is the terminal count — *SPY · Swing: 8 signals reached the board.* The two spread windows run six short-strike stages (In the delta band · Priced · Under the delta ceiling · Inside the expected-move window · Short leg liquid · Width found) then the spread stages (Spreads built · Past the momentum veto · Kept by the per-symbol cap · With the regime pass · Past the regime filter · Past the volatility floor · Past the dealer-gamma gate · Reached the board); `DIRECTIONAL` has no strike tally and runs Candidates built · Past the volatility gate · Above the quality bar · Kept by the per-symbol cap · Reached the board. ⚠ Three of those are **not** subtractions and must not be rendered as such: `kept_after_cap` is ABSOLUTE and already carries the iron condors built from the survivors (so the count can RISE), `regime_pass_added` adds, and `emitted` is read off the finished list, so it is terminal even where the chain above it does not add up. The *Width found* row is likewise read from the tally rather than derived, so a dropped width reason cannot inflate it; its headline names the largest `width_reasons` entry, ties broken by `WIDTH_STAGES` order (the search order — reporting a later stage would send the reader past the real wall).

⚠ **Every "cannot say" case reads as WORDS, not as a column of zeroes**, and the page owns none of them — it never touches the tally, which a test pins at source level (no counter name and no `strikes`/`spreads` indexing anywhere in the panel). The cases, each its own sentence: a symbol the scan never reached or an entry that is not a mapping (*This symbol was not in the last scan.*); a whole-symbol `stop` (*Schwab returned no quote for this symbol.* for `no_quote`, the price-history/chains line for `no_data`, and a generic stop sentence for a code neither names); a window with no chain (*The scan could not read an options chain for this window.*); **a chain that arrived quoting no underlying price** (`underlying_zero`, checked BEFORE the strike stages so it wins — *The chain for this window carried no underlying price, so nothing could be measured against it.*), which is deliberately **not** the no-chain wording, because the expirations WERE listed and only the spot was missing, and the zero-filled tally would otherwise render as "no expiration in this window was listed in the chain"; a missing bucket, or a bucket whose stages all dropped (*The scan recorded no account of this window for this symbol.*); and a crashed single-leg build (`build_failed` → *the single-leg build failed for this symbol; the scan logged the error*). A counter that is absent, junk or was never written is SILENCE — `funnel_view._count` answers `None` for anything that is not a real non-negative integer (`fmt.num` rejects bool and NaN) and the stage is DROPPED, not rendered as 0. ⚠ A funnel published before `underlying_zero` existed still reads, `strikes: {}` included.

**Seen since / Score trend (2026-09-17).** Two columns on all three tables, between
Checks and **Dropped at** so the lifecycle reads as one cluster: **Seen since** =
`HH:MM · Nx` (first seen, scans live) and **Score trend** = `▲ +4.2` / `▬ +1.0` /
`▼ −6.1` / `new` (`pages/options/persistence.py`: window 4 readings, deadband 2.0,
`new` under 4). Stamped by `scanner.stamp_persistence`, joined by `id` like
`stamp_stale`, over the envelope's **`setups`** map reached through each row's
`setup_key`. ⚠ The invariants: `setup_key` (`SYMBOL|TYPE|EXPIRATION`, strikes
excluded) is stamped **Tier-2 side** by `merge_day_signals` and never derived here, so
there is no cross-tier mirror; it is a **lookup, never a row key** — row identity,
"New" and the Paper button stay on `id` (`_sig_key` documents the bug a coarse row key
caused); a setup whose start was not observed carries `age_unknown` and renders `—`,
never a `first_seen` stamped *now* (the 2026-07-16 `first_seen` was deleted for lying
on cold start); an absent map dashes every row — "no reading", never "all new". A
**dropped** row is stamped too, frozen at what it was. The map is merged apart from the
row lists (a fresh row replacing a carried one cannot erase it), pruned **after** the
row cap and never of a key a surviving row references, and built under its own
`try` → `_degrade.degraded("options.merge_setups")`, so a persistence bug empties the
map rather than freezing the day union. Readout only: no sort, filter or checklist
line. Design: [`2026-09-17-signal-persistence-design.md`](plans/2026-09-17-signal-persistence-design.md).


## `/options/matrix`

Opportunity Board (**NEW 2026-07-20** — a **main-menu (left-rail) item directly under the Options group** (`main.OPTIONS_RAIL`, standalone page, NOT an Options tab-strip entry): at-a-glance **sortable grid of every watchlist stock** (~45 symbols = `collection_symbols()` minus `$VIX`), one row/symbol — Ticker/Spot/Day %/**Intraday trend**/**Call+Put flow acceleration**/P/C ratio/Net premium $M/**GEX regime**/# Signals/# Flow alerts/**Buy-Neutral-Sell** flow composite/**Hotness** (default sort, hottest first). Pure Tier-1 reader of **`cache:options:matrix`** (`webgui/pages/options/matrix.py`, version-polls ~2 s, in-place sortable `ui.table`, Tailwind-first colored cells) published by a new `options_svc` aggregator — pure `services/options_svc/matrix.py` (trend/accel/composite/hotness) + `compute.build_matrix` over `gex_history.db` (`load_flow_series` + cheap `latest_flip`) + per-symbol counts from `scan_day` (signals) + the **uncapped** `flow_alert_cooldowns` seen-map (flow alerts — not the capped `flow_alerts` rolling list, `_FLOW_ALERTS_MAX`=300 since 2026-08-09); built on the 1-min GEX branch + a ~30 s live spot/day% overlay on the header tick. Counts gate on `session_date`. See the 2026-07-20 "Last updated" entry)

**The symbol cell opens its `/symbol` dossier (2026-09-18).** A dotted-underline link
(`matrix.dossier_route`, the symbol through `shared.symbols.clean_symbol` first; a
refused symbol stays plain text), emitted from the table slot and navigated with
`shell.navigate_to`. ⚠ The board is also published as the public `/opportunity`
screen, where `/symbol` does not exist — so the link slot is used only where
`shell.can_navigate("/symbol")` is true, and `matrix.py` names the route as a string
rather than importing `pages.symbol`, which would pull the dossier into the public
process's import closure.

## `/options/flow`

Flow Alerts (**NEW 2026-08-09** — a **main-menu (left-rail) item under the Options group** (`main.OPTIONS_RAIL`, standalone page, NOT an Options tab-strip entry — it's a market-wide read, not a step in that strip's per-signal find→analyze→track→repair workflow): the **durable view of today's options-flow alerts**, which until now only chimed + toasted (miss the toast and the alert was gone; the only trace was the Opportunity Board's per-symbol count). Pure Tier-1 reader of **`cache:options:flow_alerts`** (`webgui/pages/options/flow.py`) — **no new service, command, or cache key** — version-polling ~2 s: a chronological table **newest first** (the service appends oldest-first) — Time (CT) / **Age** / Symbol / Type / Side / Detail / Alert — over the FOUR detector types (**Crossover** premium-lead flip · **Unusual activity** contract vol-vs-OI · **Gamma flip** spot crossing the dealer flip · **Big delta** one contract holding an outsized share of the symbol's gross exposure, which carries the **Share** column), with per-type `alert_detail` cells and rows tinted from a finite `(type, side)` → Tailwind class map bound via `:class` (Tailwind-first, no `:style`). Kind + symbol filters run **client-side** over already-read rows, so toggling is instant. **ONE 2 s timer serves two cadences**: the payload is re-read only when the cache VERSION moves, while the **Age** column recomputes against the rows already on screen — age stays live without churning the table. **Row click → Dealer Positioning for that symbol** (`handoff.send_to_gamma` + a one-shot stash consumed at `gamma.render()`'s build-time symbol sync, which already sets the dropdown BEFORE wiring `on_value_change` — so the handed symbol beats the cached one without a spurious refresh, then one explicit `_request_refresh()` moves the snapshot to it). **Two Tier-2 lines came with it** (both confirmed against the live key, which held exactly 50 alerts of which only 18 had a timestamp): `_FLOW_ALERTS_MAX` **50 → 300** (50 dropped the morning's alerts on a busy day) and a **`ts` stamped on UOA alerts** in the drain loop — `flow_alerts.detect_uoa` never emitted one, so unusual-activity alerts had **no time at all** while crossover/gamma_flip did. ⚠ UOA timestamps appear only on alerts published AFTER an `options_svc` restart; older rows legitimately render a blank Time. **Today only**, resets overnight; no badge, no history, and the toast/chime/phone-push/Settings toggle are unchanged)

## `/options/captured`

Captured Signals (Tier-1 reader of `cache:options:captured` + `…:captured_flags`; table + shared detail panel, per-row Expected Move, and the `captured_reload`/`captured_reprice`/`captured_close` commands). **Default order is newest capture first (2026-08-19)** — page-side in `sort_newest_first`, which **parses** `first_seen_ts` rather than comparing the strings, since the stored offset shifts with DST and the displayed `Opened` column is truncated to the minute; undated signals trail in service order on a stable sort. **A day footer sits under the table** (inside the table box, so the 70vh body scroll never carries it away and the reprice busy-overlay covers it): **opened today · closed today · P&L today (booked) · P&L today (open)**. The first three ride in the payload's new **`day`** block (`compute.captured_day_summary` → `signal_db.count_opened_on` + `get_outcomes_for_date`, both dated in **CT** because that is the timezone `first_seen_date` and `close_date` are written in); "opened" counts CAPTURES, so a signal taken and closed the same session appears in both counts. **The open P&L is summed PAGE-SIDE** (`open_pnl`) off the very `signals` list the table renders, so the footer can never disagree with the P&L column above it. ⚠ **It reads an em dash, not `$0.00`, while nothing is priced** — the persisted view carries no marks until a reprice runs (`signal_marks` is written only by the auto-manage cycle, and has not been written since 2026-06-17 on this box), so summing all-None to a confident zero would report a flat book where there is no reading at all; an genuinely empty book still shows a true `$0.00`, and a partially-priced one carries a hover note naming the coverage. ⚠⚠ **All three writers of `cache:options:captured` go through `handlers._publish_captured`** — `refresh_captured`, the `captured_reprice` command and `remove_closed_from_captured` — because two of them REBUILD the payload rather than editing it and so silently dropped the `day` block, a failure visible only by opening the page after one of those actions. The summary is re-read on every publish rather than carried forward, since a close changes the day's closed count as it happens. A source-level test pins the single `cache_set(CACHE_CAPTURED,` call site.

## `/options/paper`

Paper Ledger (ledger table + shared detail panel. **Live unrealized P&L** — `compute.paper_trades_view(reprice=True)` reprices OPEN ledger trades via `signal_repricer` (per-spread × qty), **market-hours gated**, on reload + the 5-min manage tick; the **P&L** column shows realized (closed) or live unrealized (open), **2-decimals + green/red colored**; **Credit/Risk** show 2-decimals; headers are **Credit / Risk / P&L** (no `$`); **newest-first** default sort; **Delete / Delete-all-closed buttons are red** (needed `color=None` so `.pt-danger` beats Quasar's `bg-primary`); the **Analyze** button pops a **descriptive dialog** (verdict + rationale + unrealized P&L / % / current price / DTE / target / breakeven + close X) — `compute.analyze_paper` enriched with `rationale` + `metrics`; row-click analyses update the detail panel silently. Detail panel: the **speedometer falls back to PoP** for paper trades (was stuck at 0 — no stored composite score) and the "Underlying" label is now **"Current price"**)

**Ledger risk caps (2026-09-15).** New trades (from the Market Scanner and Strategy Finder Paper buttons) are refused — nothing written — when a `shared.book_caps` rung binds against this ledger's open trades: $750 per trade (`config_paper.LEDGER_MAX_RISK_PER_TRADE`), 3 positions / $750 per symbol, 5 positions / $1,500 per sector, 5 positions per expiry across the book, and open max loss ≤ 20% of equity ($25,000 + realized P&L of closed trades — so **Delete all closed** moves it). The answer is published to **`options:paper_create`** and toasted on the sending page, not here. This ledger's open book is also published to **`options:ledger_caps`** at the end of every refresh, which the sending page's Paper dialog reads to preview every limit before the click.

## `/options/shares`

**Shares — the paper account's equity inventory (NEW 2026-09-05).** In the Options group's *track* phase beside the two book views (`OPTIONS_CHILDREN`, tab colour periwinkle `#7c6ff0`). **A second READER of `cache:options:paper_account`, not a second book** — the lots ride the account view `/options/portfolio` already reads, so one database has one publish cadence and a lot cannot exist on one screen and not the other. A separate cache view would have been two cadences over one store. Tier 1 cannot open the store itself, so `compute.paper_account_view` carrying `lots` is the only way `equity_lots` reaches a page at all. Republished on every paper-account refresh — the hourly entry/manage cycle and every manual paper action.

**Where the rows come from:** put assignment. A cash-secured put that settles **strictly below** its strike is converted by `paper_engine._assign_shares` into 100 shares per contract at the strike, and the lot is inserted at `cost_basis = strike`. The put itself is opened from the **Income** board (`income_open` → `compute.open_income_position`), which is the only production path into a lot. Read-only — lots are created and closed by the engine's settlement pass, so there is nothing to press.

**Where they GO: a covered call finishing STRICTLY above its strike** (`paper_engine.is_called_away`, the exact mirror of `is_assignment`'s strict `<` — a call settling *at* its strike is worth nothing and is abandoned, so relaxing to `>=` would deliver stock for a contract that expired worthless). `_call_away_shares` closes the lot at the **strike** via `close_equity_lot(exit_reason="called_away")`, and there is no other exit: **selling a lot by hand is not built.** ⚠ **The cash moves in TWO pieces and only their sum is `strike × shares`** — `credit_cash(cost_basis × shares)` returns the conversion (the mirror of the `debit_cash` that bought it) and `realize_pnl` carries the gain, because `realize_pnl` moves cash as well as booking P&L. Crediting the full `strike × shares` and *then* booking the lot's P&L credits the gain twice, and the lot, the exit price and the share count all still read correctly — only the balance is wrong. That is the disposal-side mirror of the double *release* the assignment path is guarded against, and the assertion that catches either is `cash + reserved + equity_at_cost == start + realized_pnl` (`options-scanner/tests/test_called_away.py::test_the_full_wheel_turns`, which drives the whole loop end to end: CSP → assigned → call written → called away). ⚠ The lot is resolved by **symbol + an exact share match** (`lot_for_call_away`, oldest first) rather than by a stored link — `close_equity_lot` disposes whole, so an inexact match is not a lot this can close, and the open path enforces the same equality so the exact lot is there by construction. With no match the option still settles and the lot is **left alone with a WARNING**, never guessed at. ⚠ `_position_legs` needs its `is_covered_call` branch: the fallback below it reads a call-side strike as an iron condor, and a covered call stores its strike in exactly that field, so without the branch a one-leg position is charged **four** legs of commission.

**Columns:** Symbol · Shares · Cost basis $/share · Cost $ · **Mark (not tracked)** · Unrealized $ · How acquired · Held since · Covering call. Not re-sorted page-side: `fetch_open_lots` returns oldest first, which is the order the lots were acquired and the order a holding period reads in; the headers stay click-sortable. `Held since` keeps a full ISO date rather than the `MM/DD` the option tables use — a share lot has no expiry and can sit for years, where a bare month/day is genuinely ambiguous. Dollars use thousands separators rather than `fmt.fixed`, since a lot's cost runs to five and six figures.

**⚠ THE LIMITATION: there is no live equity mark, and the column header names the reason.** Nothing in this application re-prices a bare share — the account snapshot carries cash, reserved buying power and the OPTION book's unrealized, and nothing in it prices stock — so **Mark** and **Unrealized** render an em-dash on **every** row today, and the unrealized cell's tone is the neutral member of its fixed three-class palette. Rendering cost basis in the Mark column, or a `0.00` unrealized, would fabricate exactly the reading the page is opened for. `lot_rows` **does** read a `mark` off a lot if one is ever attached upstream, so filling those columns later is a service change with no page edit.

**⚠ Coverage is recorded per SYMBOL, not per lot.** The paper book stores no link from a covered call back to the lot it was written against, so `covering_call` matches on symbol and **the same open call is shown against every lot of that symbol**. With two lots of one name and one call written, the screen cannot say which hundred shares are covered — because the book does not. Showing it on all of them is the honest rendering of what is stored; hiding it on all but one would imply those shares are uncovered. ⚠ Matching on symbol **alone** is the trap the other way: an open call **credit spread** on the same symbol is not a call written against the shares, and reporting it as one would tell a reader the position is covered while the shares are still naked to the upside — which is the single reading this column exists to give. Hence `COVERED_CALL_STRATEGIES`, restated Tier-1-side rather than imported (no engine or service import). ⚠ The same word `"COVERED_CALL"` now lives in **three** tiers as **two different fields** — a scan-row `type` (`compute.COVERED_CALL_TYPE`) and a paper-position `strategy` (`paper_engine`'s and this page's `COVERED_CALL_STRATEGIES`) — and they are a real mirror rather than a coincidence only because `open_income_position` stores the row's type as the position's strategy. A drift there does not merely mislabel a column: it produces a covered call the settlement branch cannot recognise, which is a lot that can never leave the book. `shared/tests/test_cross_tier_mirrors.py` pins all three (this file previously *claimed* to be "pinned by a test on both sides" when no such test existed). `covering_text` renders `210c 10/16`, reading the call strike from `call_short` **first** (a row naming the call side explicitly is the field to believe), with `×N` only when N > 1 — a single contract is the default, so stamping `×1` everywhere would hide the case that matters. A call with neither field readable renders **"Call open"** rather than a dash, since a dash would report the shares as uncovered.

**`How acquired` is provenance, and it is not decoration.** *Assigned* (`source='assignment'`) means a short put was exercised, so the cost basis is the strike that was sold and not what the stock was worth on arrival; *Bought* (`'manual'`) means it was entered by hand. Anything else — **including an absent source** — is a dash: defaulting an unknown provenance to "Bought" would assert a fact the row does not carry.

**The status line has FIVE states, four of which are empty tables**, and they are four different facts: a cold feed (`copy.WAITING_OPTIONS`), an account never opened (`has_account is False`), a payload written before the inventory field existed (`"lots" not in payload` — absent is **not** empty, and reading it as zero is the number-nobody-read this app refuses to print), and a book that genuinely holds no stock. Wording any of them with the shared cold-feed sentence would report a healthy account as an outage. The populated line sums only what reads — a lot with an unreadable share count is left out of the totals rather than counted as zero, and the lot count still shows it.

**Two invariants from the store side worth knowing before editing this page.** `equity_lots` never holds reserved buying power — a lot is cash already **converted into shares** — because `reconcile_buying_power` recomputes `buying_power_reserved` from the OPEN `paper_positions` sum alone and would silently zero anything else. And `session_start_equity` now includes `Σ(open lots: shares × cost_basis)`, at cost only; share unrealized stays out, matching how options are treated.

## `/options/calculator`

**Every expiration, strikes on demand (2026-09-12, same day).** The page sends `calc_load` with `lazy: true` and `expiries` (a restored/handed-off expiry, pending and on-screen leg expiries, the selected pill); the payload gains `expirations` (every listed expiry, from Schwab `/expirationchain` via the proxy's `/passthrough`) while `chain` holds only the nearest two plus those. A pill whose strikes are not loaded enqueues `calc_load_expiry` and parks the move in `state["pending_move"]`; the merged payload arrives marked `added` and `_merge_chain` moves the legs WITHOUT re-seeding (or, on `failed`, reverts the pill and says so). `panel.set_chain(…, expirations=…)` draws every pill; `panel.is_loaded(expiry)` answers "have its strikes arrived". Rescue's ad-hoc `calc_load` sends no `lazy` and keeps the old today..+60 fetch. Measured before building: the list 0.2 s, one `$SPX` expiry 0.6 s, TSLA's whole chain 4.3 s / 5.6 MB — and `$SPX`'s 60-day fetch failed outright at the proxy.

**The entry panel (2026-09-12) supersedes the layout described in the next paragraph.** The page is now the shared `pages/options/entry_panel.py` across the top — ticker (Enter / tab-out loads; REFRESH re-pulls), strategy picker, an **expiry strip** of pills (clicking one = `editor.apply_expiry` + `refill_prices`), and the **chain grid** — the COMPLETE chain for the selected expiry (`chain_grid.chain_grid_rows` with no window) rendered as ONE `ui.html` block by `chain_grid.grid_body_html`, whose Bid/Ask cells carry `data-pick`/`data-side`/`data-strike` for a single delegated click listener (`entry_panel._PICK_JS` → `pick_from_args`), the at-the-money row `data-atm` for `entry_panel.center_js`, which scrolls ONLY the body to it after every paint; default columns Delta · OI · Volume · Bid · Ask on the call side, mirrored on the puts BESIDE the leg editor's new **`layout="table"`** — then a collapsed **PRICING ASSUMPTIONS** row (Price, IV %, Rate %, IV Δ %, Contracts, Strikes — the same widgets under the same names), then the metric cards and P&L matrix at full width. **There is no action grid**: LOAD CHAIN, IV UPDATE, FETCH PREMIUMS and CALCULATE are gone. A landed chain lays the template on `panel.selected_expiry()` (`apply_template(near=…)`), prices every leg (`_price_for` → `extract_premium`, a share leg → spot while unset), and runs `fetch_iv` silently; every edit pokes `entry.Debounce(RECALC_DELAY_SEC=0.3)` and the 0.1 s `_recalc_tick` timer enqueues `calc_compute`. A grid click → `entry.leg_from_pick` → `editor.place_pick`, which MOVES the leg on the same side and type (`entry.pick_target`; nearest strike among several) and adds one only when none matches, ⚠ **priced at the row's price source — the MARK for a new row** (the column decides only long/short). Each leg row has a **Bid / Mark / Ask** dropdown (`_price_source`, private, stripped by `normalize_legs`; `_price_for(leg, source)` → `chain_grid.extract_price`) and a strike DROPDOWN (`strike_choices`; not `with_input` — Quasar's 50px filter-box min-width sat under the ‹ button) since 2026-09-12. A typed price sets the leg's private `_manual_premium` flag and survives strike/expiry/type changes until its ↺. The expiry that used to be `expiry_sel` is the panel's; a restored or handed-off expiry rides `state["pending_expiry"]` into `panel.set_chain`. Chain-grid columns persist in `app_settings` `chain_grid_columns` (`chain_grid.parse_columns` always keeps Bid and Ask). The chain readers (`extract_premium`, `extract_delta`, `leg_delta`, `chain_expiries`, …) moved to `pages/options/chain_grid.py`; `calculator` re-exports them by name. ⚠ Every one of them matches the chain's **exact** strike (1e-6 for float noise) since 2026-09-17: the old "within 0.51" tolerance swallowed a half-strike ladder — on UBER's 09-18 expiry, which lists 72.0 and 72.5, a 72.5 leg was priced, Greeked and IV-implied off 72.0 (mark 0.19 against its own 0.13). A strike the expiry does not list is an em-dash, never the neighbour's number. `cache:options:calc_chain` now carries ten fields per contract (adds gamma, theta, vega, openInterest, totalVolume). Design + plan: `docs/plans/2026-09-12-calc-sim-entry-panel-{design,plan}.md`.

Calculator (**rebuilt 2026-08-19 to a three-step screen** — ① STRATEGY and ③ LEGS fill a **fixed 424 px input column** with the four-button action grid under them; ② SYMBOL, **six metric cards** and the **P&L matrix** fill the results column beside it. The palette is the page-scoped **`[calc]`** language — scope hook **`.calc-v3`**, a near-black ground with cyan/green/amber and JetBrains Mono, deliberately NOT the app-wide `.calc-v2` navy the Simulator and Trade wear (`build_calc_tokens`/`build_calc_css`/`CALC_KEYFRAMES_CSS`/`build_calc_font_head_html`; the mono `<link>` is added per-client inside `render()`, so no other route requests it). **Tier-1 reader, and the redesign needed NO Tier-2 change**: it still enqueues `calc_load` / `calc_compute` / `calc_iv` on `cmd:options` and version-polls `cache:options:calc_chain` / `:calc_result` / `:calc_iv` at 1 s each (the ~7000-contract chain payload is read OFF the loop via `run.io_bound` behind an in-flight guard; IV and Fetch premiums run the pure chain-extractors LOCALLY on the cached chain and enqueue nothing). Every readout the design added is derived page-side by a PURE function over what the service already caches. **The matrix `%` column changed meaning, and now NAMES ITS OWN BASIS in the heading** (`matrix_basis`, resolved ONCE per render so the cells and the heading above them cannot come from different decisions): **`% MAX`** — a share of the summary's `max_profit` — when that is a usable denominator (finite, > 0, and not the uncapped sentinel) **and the legs are not net-long-call**; else **`% COST`**, a share of the debit paid, when `entry_credit` is negative so that `-entry_credit` > 0 — the same figure the ENTRY DEBIT card shows, so column and card agree by construction; else a bare **`%`** over em-dash cells: no basis, no claim. It was a share of *premium received* (`calc_spread_pnl`'s `pnl_pct`, which the service still emits and the page now ignores); for a credit structure `% MAX` is numerically identical, since `max_profit` IS the entry credit. ⚠ on the generic NUMERIC path (debit verticals, butterflies, calendars) `max_profit` is `max(pnl)` over the SERVICE's own price grid rather than a closed-form cap, so widening that grid can move the denominator; the analytic paths (PCS/CCS/IC + the four singles) return a true cap and don't drift. **Where that grid edge is not merely imprecise but WRONG — a net-long-call structure, whose profit has no cap at all — the LEGS override the summary** (`profit_uncapped_above`, i.e. `net_call_quantity(legs) > 0`; the mirror of the net-SHORT-call test `max_loss_estimate` already uses for unbounded LOSS): MAX RETURN renders `Unlimited` and the matrix falls back to `% COST`, whichever routing produced the summary. Without it, nudging one strike re-routes `LONG_CALL`→`CUSTOM` and the SAME position swaps from `Unlimited` / `% COST` to `$32,780 at 30d expiry` / `% MAX` against a fabricated denominator propagated down every cell (measured, SPY 668 / 670C @ 4.20 / 30 DTE). Cell text clamps at **±999%**, and the tint scales against **each side's own** grid extreme — one shared scale would wash out the profit zone of every credit structure. **Per-leg DELTA** now shows in the leg card, read from the chain's OWN `delta` field (`extract_delta` — the field `flow_alerts` reads, so it is market delta, not a second pricing model living in Tier 1) and sign-flipped for a short leg (`position_delta`); it is **PER CONTRACT, deliberately not multiplied by qty**, so it compares directly with a broker's chain. ⚠ it renders an **em-dash, never `0.00`**, when there is no reading — no chain, no strike, a strike the chain does not carry, or a value outside `[-1, 1]`, which is Schwab's **`-999.0` missing-greek sentinel**; index chains read hollow outside regular hours, so that is the ordinary case rather than the edge one (a genuine far-OTM `0.0` IS kept). ⚠ it reads the leg's OWN expiry with **no cross-expiry fallback** (`leg_delta`): `extract_delta` returns `None` both for "no such contract" and for "the sentinel is present", and `_find_contract` answers with the FIRST expiry in dict order carrying that strike, so a fallback would paint a December leg's cell from an August contract — and it could never help anyway, since `leg_editor._render` coerces every leg's expiry into `expiries_for()` before the body reads it. **The ③ LEGS frame's chip row carries a live strip** — leg count · `NET` · `MAX LOSS` (`leg_strip_facts`, abbreviated K/M past 100k because the frame is 424 px wide and a naked put on $NDX runs to millions). ⚠ both figures read as em-dashes in ordinary use and must: `net_premium` is `None` while ANY leg is unpriced (which is every fresh template — `build_default_legs` sets `premium: None` — until Fetch premiums runs), and `max_loss_estimate` is `None` when the loss is unbounded (a net SHORT call quantity) or undecidable (a short leg outliving a long one, which its single-date model cannot see). Max loss is otherwise EXACT rather than a width heuristic: the expiration payoff is piecewise-linear with corners only at the strikes, so evaluating `net_premium` + intrinsic over `{0} ∪ strikes` finds the true minimum — an iron condor risks ONE side, a 1-2-1 butterfly needs no case of its own, and a lone short put reads its real `strike × 100 − credit`. **The six metric cards** (`metric_cards` — always six, so the grid never reflows between renders) are, in order: **ENTRY CREDIT/DEBIT** (the sign picks the label; the sub-line is contracts × legs — the **smallest** leg qty, because `_scale_leg_qty` takes the Contracts field onto the legs by multiplying the whole set and every template's smallest leg is 1, so the minimum reads that field back exactly where the maximum called a 1-2-1 butterfly "2 contracts"; and it is the only place position size is stated) · **MAX RISK** · **MAX RETURN** · **RETURN ON RISK** (with a per-day figure dated off `max_dte_from_legs`, since the payload carries no horizon of its own; `max(dte, 1)` so 0-DTE is not a division by zero) · **BREAKEVEN(S)** (with the first crossing's % from spot) · **PROB OF PROFIT** (lognormal, risk-neutral drift — naming the model is what stops it reading as a forecast). **Every card degrades to an em-dash rather than to a zero**, and **`Unlimited`** is rendered wherever the service returns its uncapped `999999` sentinel — a long call's max return, a naked call's max risk — never `$999,999`; RETURN ON RISK reads an em-dash when either side is uncapped, since the service sends `0.0` there and that would read as a measured zero return — **and equally when `max_loss` is `0.0`**, the engine's own third condition (`max_loss in (0, UNLIMITED) or max_profit == UNLIMITED`). **MAX RISK em-dashes that zero too**: out of the numeric path a `0.0` max loss means the price grid never reached the loss, not that there is none — measured, a far-OTM short put returned `max_loss 0.0` while the ③ LEGS strip, which solves the payoff exactly, read $29,965 for the same legs, and `$0` in red under "worst case at expiry" is the most dangerous figure this page could print. **Loading a DIFFERENT symbol now drops the on-screen result**, so the cards and matrix fall back to the placeholder instead of standing under a pill announcing another symbol; **reloading the SAME symbol keeps them**, which is what the restore-on-navigation path does on every return visit. The screen has TWO waits and names which one it is at ONE decision point (`chain_status_facts` → `results_panel_facts`): **AWAITING CHAIN** vs **AWAITING CALCULATION** behind a dashed panel, with the title-bar pill (`AWAITING SYMBOL` / `LOADING CHAIN` / `CHAIN LOADED · SYM`), the ②/③ frame accents and the scan bar all following the same facts — an EMPTY chain dict is `idle`, not `ready`. ① STRATEGY carries **tag chips + a one-line thesis** for all **18** templates (`strategies.strategy_tags` / `strategy_blurb`); the leg-count chip is DERIVED from `STRATEGY_TEMPLATES` so it cannot disagree with the legs built, and only the FIRST chip is coloured — by cash-flow direction alone (`tag_tone`), since colouring "BULLISH" green would read as an opinion the page has not formed. Legs are the shared `leg_editor` in **`layout="card"`** with the `[calc]` palette injected as `tokens`, a **`min_legs=1`** floor and a **RESET TO TEMPLATE** button beside ADD LEG (a two-leg floor would put the four genuine single-leg templates out of reach by hand; the mock's floor of two is an artifact of its own leg-padding). **The top-level Expiry was KEPT against the mock**, which has per-leg expiry only: it drives `calc_compute`'s `expiry` argument and `leg_editor.apply_expiry`'s propagation onto every leg, so dropping it would delete working behaviour to match a mockup that never had to call a service — it moved into the ② SYMBOL readout row. **Unchanged by the redesign:** grid rows = **±N real chain strikes around spot** via **Strikes** (default 24; `strikes_window`→`price_rows`); **intraday time-to-expiry**, so the first column is **"Now"** (mark-to-market, priced at calendar hours-to-4pm-ET /365) and the last is **"Exp"** (expiration payoff) — the fix that made 0DTE readable at all; **IV UPDATE** implying IV ThinkorSwim-style from the traded contract's mark via a `calc_iv` command (falling back to ATM chain `volatility` pre-strike-pick); per-leg expiry so **calendars** price each leg at its own T; **full UI-state persistence across navigation** plus auto-refresh on return (`page_state.py`); the **Symbol** field loading on tab-out (`focusout`) / Enter, deduped via `inputs.should_load`, behind the centered full-screen wait overlay (`overlay.py`, `LOAD_TIMEOUT_SEC=30 s` backstop); and **Send-to-Calculator / Copy-from-Simulator stashing legs as `pending_legs`** so they apply only once the chain has landed — applying them first wipes every strike through the editor's strike-coercion, which is exactly the "legs don't transfer" bug. The matrix itself is ONE raw `ui.html` fragment carrying inline `style=` — a few hundred cells built with `.classes()` would be a few hundred Vue elements — which is the documented out-of-scope case for the Tailwind-first rule; it is sticky-headed and sticky-price-columned, and **auto-scrolls the amber spot row into view** 0.12 s after paint — and marks NO row when `spot` degrades to `0.0` (the nearest row to zero is the LOWEST price on the ladder, which the amber rule would otherwise present as today's price). Its green/red heat ramp is a data-driven cell map and so is deliberately NOT in `config/theme.toml`, living beside the renderer instead)

## `/options/swing`

**Rate my trade (2026-09-16).** A **RATE MY TRADE** button beside EXPECTED MOVE (enabled once `_has_contracts(chain)` and `leg_editor.legs_ready(legs)`) enqueues `calc_rate` `{request_id, symbol, structure: sim_view.template_for(legs) or "CUSTOM", legs}` and opens a `ui.dialog` built at the page's ROOT (NiceGUI 3 mounts a dialog in the client layout, and one built from a cleared slot deletes itself). `options_svc.rate_trade.rate` turns the legs into a Strategy Finder row off the cached `calc_chain` (`finder_legs`: chain quotes/Greeks, the page's `premium` as the mark, quantities reduced to the structure's ratio; `CALC_TO_SCORER` maps each template to the Finder's type/family/label/bias, pinned against `STRATEGY_TEMPLATES` by `shared/tests/test_cross_tier_mirrors.py`), scores it with `score_all` WITHOUT `_passes_swing_cut` or the vol-gate drop (`vol_gate_blocks` carried instead), stamps it with `stamp_candidate(trade_type="SWING")`, and always writes `cache:options:calc_rating` `{request_id, symbol, legs, row, error}`. `_poll_rating` paints only a payload whose `request_id` matches (`rate_trade.request_matches`), reads `checks_feed.read_context` off the loop, and derives BUY / CAUTION / PASS with the PURE `webgui/pages/options/rate_trade.verdict_word` (Strong/Good + Clear → BUY; + cautions or muted → CAUTION; Marginal + Clear → CAUTION; everything else, and any Blocked, → PASS) over the shared detail panel (`detail.render(width=440)`, `detail_signal(row)`, `checklist_candidate(row, True)`). No answer in `LOAD_TIMEOUT_SEC` → a sentence; a click while one is pending does nothing. ~1–4 Schwab calls per click. Design + plan: `docs/plans/2026-09-16-calc-rate-my-trade-{design,plan}.md`.

**A large chain asks first (2026-09-14, same day).** Operator request: "for SPX (and other large chains) don't load the complete chain, give me option what to load." Live whole-chain scans (range *All*, ~12:30 CT): NVDA 25 expirations 13.5 s / 139 rows · SPY 34 → 25.7–27.4 s / 162–167 · `$SPX` 56 → 40.1 s / 69, none failed, the per-type 25 never binding — and `cmd:options` runs one command at a time, so a `$SPX` scan held up Calculator/Simulator loads whose overlay gives up at 30 s. **Service:** `swing_scan(..., expiry_choice=None, ask_if_large=False)`, `ask_if_large=True` from the Finder's handler only (the Income Window never asks). The expiration list is read once (`option_expiration_rows` → `parse_expiration_rows` → `(date, expirationType, dte)`, DTE = Schwab's `daysToExpiration`, the chain keys' own number, falling back to the calendar difference when that field is unusable or more than a day off it — degrade `options.expiration_dte`), and `_plan_fetch` counts the rows inside `dte_min`..`dte_max`. More than `LARGE_CHAIN_EXPIRIES` (30) with no choice → `needs_choice: True`, `expiration_count`, `choices` (`choice_summary`: `[{key, label, count, est_seconds}]` over `EXPIRY_CHOICES` `next_30` DTE ≤ 30 · `next_90` DTE ≤ 90 · `monthly` type `S` · `all`, `est_seconds` = count × `SCAN_SEC_PER_EXPIRY` 0.75 rounded half up) and **no chain fetch**. With a choice: `fetch_scan_chain(rows=, dates=)` fetches those dates (runs consecutive in the listing, ≤ 8 each — a monthly with weeklies listed between it and the next is its own run, far-dated monthlies that are neighbours in the listing share one) plus, when the choice omits it, the expiry the whole scan's `extract_atm_iv` would read (`_iv_reference_date`; degrade `options.scan_iv_reference` if it fails to load), and the chain is sliced back to the chosen dates after `run_iv_analysis`, so IV / Vol Rank / expected move are the whole chain's while calendars pair only inside the choice. `expiries_failed` is recounted over the CHOSEN dates only; none loaded → `chain_missing`; a choice empty in range → `no_expiries_in_range` with `expirations_scanned` 0. A choice on ≤ 30 expirations is ignored (`expiry_choice: null`); an unknown one raises `ValueError` → the handler's `error` answer. Payload adds `needs_choice`, `expiration_count`, `expirations_scanned`, `choices`, `expiry_choice`. **Page:** `fv.chooser_facts` → one `CARD` in `picks_grid` (*$SPX lists 56 expirations in this range.* / *Choose what to scan:* / buttons *Next 30 days · 23 · ~17 s*, disabled at count 0; an unread count or estimate is dropped, never 0; no symbol or no known key → no chooser, and the summary and empty lines fall through to their ordinary text); chips cleared; `summary_facts` counts *56 expirations — choose what to scan*; `no_data_label` *Choose which expirations to scan for $SPX.* A pick (`_on_pick`) scans the CARD's symbol (writing it into the box and `inputs.mark_symbol_loaded` so the tab-out dedupe agrees) and stores `state["choice_by_symbol"][SYMBOL]` — page state, never persisted — which `_request_scan` sends as `expiry_choice` (the key is omitted, not null, when there is no pick). A scan with a choice applied ends the count line *Scanned 19 of 56 expirations · Monthlies only* (`can_change`) beside a **Change** link (`CHANGE_LINK`) that repaints the chooser from the answer's `choices` without scanning. A remembered pick holding nothing in the new range reads *Next 30 days holds no expirations in this range for $SPX — use Change to pick another.* (the Change clause only when Change is drawn). The stale guard is `swing.answers_request`: `payload_answers_scan` plus an EXACT `expiry_choice` match against the echoed params, so the plain scan that asked cannot paint over the pick after it. The scan-timeout `ui.timer` is mounted in `list_box` (never cleared): a pick's sender slot is the cleared chooser, where the timer would be deleted and never fire. The DTE boxes are `DTE_BOX` `w-24` (`w-20` clipped *no limit*). Design + plan: `docs/plans/2026-09-14-strategy-finder-large-chain-chooser-{design,plan}.md`.

**Whole chain, every expiry (2026-09-14).** Operator request: remove the 120-day restriction and consider the full chain. Three service keywords on `compute.swing_scan`, all passed by the Finder's handler alone (the Income Window passes none): **`every_expiry=True`** (`_build_every_expiry` — each single-expiry builder runs once per listed expiry in the window on the chain sliced to it with `dte_min = dte_max = d`, so no builder changed; calendars take each expiry ≥ `_MIN_FRONT_DTE` as the front over a slice of it plus the expiries ≥ `_CAL_MIN_GAP` later, keeping only rows fronted there; credit spreads stay one `screen_spreads` pass; iron condors pair per expiry), **`earnings_mode="flag"`** (`screen_spreads` gets no date; every row the drop would remove — `earnings_gate_applies` + `check_earnings_conflict` against the LATEST leg expiry — is kept and stamped `spans_earnings: True` + `earnings_date`) and **`per_type_limit=FINDER_PER_TYPE_LIMIT` (25)** (after the quality cut, the best 25 of each `type` by `composite_score`; the rest counted as `not_shown`; ids and payoff curves built after). **Every `swing_scan` caller** fetches through `compute.fetch_scan_chain` — the expiration list, then runs of ≤ `SCAN_RUN_EXPIRIES` (8) consecutive listed expiries from TODAY to today + `dte_max` + 2 (all when `dte_max` is `None`), ≤ `SCAN_FETCH_WORKERS` (4) at a time, raw expiry maps merged; a run that returns no expiry adds its EXPIRIES to `expiries_failed`, and no expiration list falls back to the single fetch with `expiries_failed = None`. The quote is read BEFORE the chain so `spot` survives a missing chain; a missing chain or a Schwab `status: "FAILED"` body (a mistyped symbol) returns early with `chain_missing`, or `no_expiries_in_range` when the list loaded with nothing in range. **The handler always answers**: a raising scan is `_degrade.degraded("options.swing_scan")` and publishes `signals: []` with `error` = the exception's CLASS name (readers test truthiness; the traceback is in the log). Payload adds `not_shown`, `spot`, `chain_missing`, `no_expiries_in_range`, `expiries_failed` and `error?`; args accept `dte_max: null` (a MISSING key still takes the handler default — `dte_min` 5, `dte_max` 30, each separately; the page always sends both). **Page:** presets *1–2 wk* 7–14 · *2–6 wk* 14–42 · *1–3 mo* 30–90 · *3–12 mo* 90–365 · *1 yr+* 365–none · **All** 0–none (default; `DEFAULT_DTE`), a blank DTE max (placeholder *no limit*) sends `dte_max: null` and a blank DTE min sends 0 (`scan_params`); `scan_controls_from` restores an explicit `null` max, while a missing one falls back as a bad pair. `summary_facts` always has a price (`_spot`: the payload's `spot`, else the first row's `underlying_price`; `PRICE_UNAVAILABLE` "Price unavailable" otherwise), adds *N lower-scoring ideas not shown* and *N expirations could not be loaded* (None adds nothing), and reads **Scan failed** for an `error` answer. `no_data_label` precedence: error (*The scan for SPY failed. Check System Status and scan again.*) · `chain_missing` · `no_expiries_in_range` · quality cut · too cheap · nothing built, each naming *SYMBOL at $price* (the *at $price* dropped when no price was read; the error sentence names the symbol only; `no_expiries_in_range` looks from today to `dte_max` + 2 and ignores `dte_min`). `earnings_text` renders *Earnings Nov 19* (year added when it differs from today's) as a `BADGE_WARN` pill on the card and an interpolated (never `v-html`) tag in the Strategy cell. **Waiting:** the spinner (`build_busy`, `elapsed_label` → `scan_timeout_text`, *Scanning SPY… 12 s*) and placeholders stay until `payload_answers_scan` — no longer the shared 30 s backstop; `SCAN_TIMEOUT_SEC = 180` is the one ceiling (the spinner's own deadline sits 5 s past it so `_scan_timed_out` alone ends a wait and shows `SCAN_SLOW`). **Paging:** the list is server-side paged, `PAGE_SIZE = 50` — `page_of` sorts the WHOLE row set on the column's numeric twin then slices, `rowsNumber` puts Quasar in server mode, `request` events repaint one page, and payoff shapes are built per page (`list_rows` / `with_shapes`); a new list starts on page 1 in the last sort, and `_table_batch` makes a paint one table update. Measured on a synthetic 510-row answer: ~1.96 MB of rows (~1.4 MB of it payoff SVG) against ~190 KB per 50-row page. Design + plan: `docs/plans/2026-09-14-strategy-finder-whole-chain-{design,plan}.md`.

**Redesigned 2026-09-13 — top picks, a slim list, chips and presets.** Top to bottom: a **scan bar** (Symbol + Scan — the box starts on the cached result's symbol (`swing.initial_symbol`, `SPY` when nothing is cached), read before the bar is built because `bind_symbol_load` seeds its tab-out dedup from the box's value at bind time, and the Expiry / Risk style / Advanced fields start on that result's echoed `params` via `finder_view.scan_controls_from`, each group falling back to the page default as a group; **Expiry** presets `finder_view.EXPIRY_PRESETS` *1–2 wk* 7–14 · *2–6 wk* 14–42 · *1–3 mo* 30–90 · *3–12 mo* 90–365 · *1 yr+* 365–no limit · *All* 0–no limit (since 2026-09-14; the redesign shipped *Any* 0–120) beside DTE min/max — editing a box clears the preset via `expiry_preset_for`; **Risk style** `RISK_STYLES` *Conservative* 0.05–0.10 · *Balanced* 0.10–0.20 (default, = the pre-redesign band, so the default scan did not move) · *Aggressive* 0.20–0.30, writing all four band fields, with a read-only *Custom* marker when the Advanced fields match no style — `risk_style_for`, never a selectable option; the **Advanced — delta bands and credit floor** expander keeps the raw fields; no control rescans), a **summary strip** (`summary_facts`: symbol, price, direction / conviction / volatility pills, **Vol Rank** — moved out of the table, one value per scan — and the count line *N ideas · K below the quality bar · J where premium is too cheap to sell · M lower-scoring ideas not shown · F expirations could not be loaded*, `filtered_out` and `vol_filtered` kept as two sentences), **strategy chips** (`chip_counts` / `toggle_chip` / `filter_groups` over each row's service-stamped `group`; instant, no rescan; un-choosing the last chip returns to All; `carry_chips` keeps the choice across a same-symbol repaint and resets it for a new symbol), up to four **top-pick cards** (`top_picks`: the best score from each DIFFERENT group among the visible rows first, then — when fewer than four groups are visible, e.g. one chip — the next-best remaining ideas in score order, so every slot fills; score (`score_text`, the same whole number as the list badge, which still sorts on the raw score) + grade, expiry, legs, payoff shape, split risk/reward bar, probability-of-profit bar, cost, Calculator + Paper where `_PAPER_TYPES` allows; a click opens the detail panel), and the **ranked list** (`finder_columns`: Strategy with a 72×20 payoff shape · Score · **Strikes** (`strategy_table.legs_summary`, injected as `finder_rows(legs_text=)` so it is the card's legs line; not sortable. ⚠ Strikes and Cost WRAP (`whitespace-normal` + a `min-w` floor) and every header may wrap — unwrapped, a condor's legs pushed the table 200 px past its box at the app's 1440 px width and hid the actions. The spaces inside a leg and before each slash are non-breaking, so a line ends `S 530C /`) · Expiry · Cost · Max profit · Max loss · Probability of profit · Grade · actions; the number columns sort on numeric twin fields `_dte` / `_max_profit_n` / `_max_loss_n` / `_pop_n`; the 65vh table cap is lifted for this table by `FINDER_CSS`). Breakevens and bias moved to the shared **detail panel** (Strikes came back to the list the same day, on request), which starts collapsed, opens on a card or row click and is cleared by a new scan. **The page stops sending `families`** — every scan builds all seven groups (the service's `None` default). **Loading:** `_request_scan` clears the old results and paints four placeholder cards reading `scanning_text(symbol)` (*Scanning SPY…*); when `SCAN_TIMEOUT_SEC` (180 s) passes with no result the placeholders become ONE still card (`waiting_text`, or `copy.WAITING_OPTIONS` when nothing has published this session) and the status reads "The scan is taking longer than expected. It will appear here if it finishes; if nothing arrives, check System Status and scan again." (`SCAN_SLOW`), with the request still tracked so a late result paints; a payload is painted only when `finder_view.payload_answers_scan` says it answers the request being waited on — the symbol AND every field the handler echoes back in `params`, so SPY 1–2 wk never paints as SPY 1–3 mo. **Cost** (`cost_text`) is `$195 debit` / `$804 credit` / `$54,058 debit for 100 shares`. **Pictures:** `payoff_svg` is a FIXED-pixel SVG (no stretched viewBox — `vector-effect` is stripped by DOMPurify) of one `<line>` per segment coloured by sign, split at the interpolated break-even, over a dashed zero line with a tick at spot; `risk_reward_bar` scales both halves to the larger of max loss / max profit (an unbounded side fills and reads `∞`), and `pop_bar` is amber < 40 / theme blue 40–60 / green > 60 on the ROUNDED percent; widths snap to a fixed 5% class set. **Service (additive, no contract change):** `compute.swing_scan` stamps each candidate's build `group` and attaches `strategy_scanner.payoff_curve` (points across the WIDER of spot ± 2× the expected move to that candidate's front expiry and the outermost option strikes ± 3% — so a wide-winged condor's max loss is inside the window — valued as `payoff_metrics` values the position, gross of commission; a row whose option legs all carry mark 0 gets `None`, since it would price the entry as free); `income_scan` passes `payoff=False`, since nothing reads a curve off the Income board. `strategy_table.strategy_columns()` is unchanged — the Market Scanner's Directional tab still uses it. Design + plan: `docs/plans/2026-09-13-strategy-finder-redesign-{design,plan}.md`.

Strategy Finder (**multi-strategy**, single-symbol: builds + ranks candidates across **seven build groups** on ONE unified **0–100 Fit+Quality** score — **Directional** (long/naked call+put; `SHORT_PUT` is the cash-secured put), **Spreads** (debit bull-call/bear-put + credit PCS/CCS), **Neutral** (iron condor), and since 2026-09-13 **Straddles & strangles** (`LONG_/SHORT_STRADDLE`, `LONG_/SHORT_STRANGLE`), **Butterflies & condors** (`BUTTERFLY_CALL/PUT`, `IRON_BUTTERFLY`, `CONDOR_CALL/PUT`), **Calendars** (`CALENDAR_CALL/PUT`, `DIAGONAL_CALL/PUT`) and **Stock + options** (`COVERED_CALL`, `PROTECTIVE_PUT`, `COLLAR`, one 100-share lot at spot). The four new groups are `swing_scan` `families` codes `STRADDLE`/`BUTTERFLY`/`CALENDAR`/`STOCK` — build groups only; their candidates carry `family` NEUTRAL / VOLATILITY / DIRECTIONAL, the vocabulary `strategy_scoring` reads. Strike rules (`strategy_scanner`): straddles/strangles, flies, the iron fly, condors and share structures sit on the nearest expiry **≥ max(DTE min, 7)** (`_MIN_FRONT_DTE`, applied inside `_front_pair` — operator decision 2026-09-13, so a DTE min of 0 builds none of them on a 0–6 DTE expiry; directionals, debit/credit verticals and the IC take no floor. On the Finder since 2026-09-14 every builder runs once per listed expiry, so "nearest" means that expiry); straddle, fly body and iron-fly body **ATM**, and **skipped** (never recentred) when the listed ATM strike is missing from either side; short strangle at the delta-band midpoint with the ceiling enforced; long strangle its own ~0.30-delta wings; fly/condor wings at the symmetric listed distance nearest ½ the 1-σ expected move (condor shorts ±1 wing, longs ±2), and a long fly/condor not priced for a debit strictly inside (0, wing × 100) — or an iron fly not priced for a credit inside it — is not emitted (`_priced_inside`; wide mid marks can price a long fly for a credit that reads PoP 100 and ranks first); calendar front = first expiry ≥ max(DTE min, 7) (each such expiry in turn on the Finder), back = expiry nearest front+28 that is ≥ 7 days later and inside DTE max, skipped when either month's ladder has a hole at the money; diagonal short front OTM near 0.30Δ (0.15–0.45 accepted), long back ITM near 0.70Δ, skipped when debit ≥ width; share structures front ≥ 7 DTE, call at the band midpoint with the ceiling, put near 0.25Δ, none under 0.10Δ (collar call ≥ 0.05Δ). **No extra Schwab call** — a window without two usable expiries builds no calendar. `payoff_metrics` values calendars/diagonals/share sets at the **front expiry** (back legs BS at their own IV, floored at intrinsic; shares at spot) and bills commission per option **contract**, shares free; a share structure's `capital` is the cash it ties up. The earnings gate reads a multi-expiry row's **latest** expiration. **Legs line** (on the top-pick cards since the redesign): `L 100 shares`, `S 2×100C`, a later leg dated `11/13`. **Detail panel:** `Buy 100 shares`, `Sell 2× 100 C`, each leg with its own date (and no single `Exp` caption) when the option legs span more than one expiry, every breakeven joined with ` / `, and "per position" money when shares are held. Breakevens are scored against the 1-σ move to **each candidate's own expiry** (`score_all(..., daily_move=)`), as on the Market Scanner's Directional tab. The **Advanced — delta bands and credit floor** expander holds the delta bands (every out-of-the-money short) and the min credit (spreads only). The short straddle and covered call fail `NAKED`'s 65 PoP bar and land in the cut count on nearly every scan (operator decision); `tools/sweep_strategy_gates.py` re-measures every structure. The scanner **infers a market view** (direction/conviction + IV vol-regime) from the symbol's technicals + IV and ranks each structure by FIT to that view + STRUCTURAL QUALITY — so a long call and a put-credit-spread are comparable. The seven group checkboxes, view banner and 14-column table were replaced by the 2026-09-13 redesign above; the **Grade is still quality-gated** — color-coded green/amber/red with a `grade_reason` tooltip, driven by structural quality + per-family hard gates, NOT view-fit. **Since 2026-08-06 the SERVICE only emits non-Weak candidates scoring ≥ 50, across every family** (`compute.SWING_MIN_SCORE` / `SWING_EXCLUDED_GRADES`, cut before `assign_ids`), so Weak rows no longer reach the table at all and the summary strip reports **"N below the quality bar"** (`finder_view.summary_facts` off the payload's additive `filtered_out`) — that count is what tells an all-cut scan apart from a scan that found nothing. Per-row **Send to Calculator / Expected Move** work for ALL types via the canonical `legs`; **Send to Paper** works for credit structures (PCS/CCS/IC) **AND defined-risk debit structures (LONG_CALL/LONG_PUT/BULL_CALL/BEAR_PUT)** as of 2026-07-13, and **BUTTERFLY_CALL/PUT + CONDOR_CALL/PUT** as of 2026-09-13 (`strategy_table._PAPER_TYPES` = `shared.structures.LEDGER_DEBIT` ∪ credit, pinned by `test_cross_tier_mirrors`); `create_paper_trade` refuses a debit type with no positive finite `net_debit`. No button for naked shorts, straddles/strangles (analysis only, D1 — the ledger refuses them by name), the iron butterfly, calendars/diagonals or share structures. Send to Calculator carries both expiries and share legs; an expiry pill clicked there afterwards moves every option leg to one date. See the "Multi-strategy Swing Scanner" section below)

**Paper button → Ledger caps (2026-09-15).** Same path as the Market Scanner: `paper_create` is checked against the Paper Ledger's caps, and the page toasts the answer from **`options:paper_create`** (opened, or not opened with the reason and *Up to N contracts fit.* when a smaller size would clear). The Paper dialog is the Market Scanner's, with the same `options:ledger_caps` preview over the Finder row's stamps.

**Checks column and “Only clear” (2026-09-15, Phase 4).** The Market Scanner's column and switch, through the same `checks_table` helpers over `checks_feed`'s context — so two tables cannot word or filter one verdict differently. Neither column sorts (`finder_columns` has said so since it was written; the Scanner's joined it on 2026-09-15), and both hover `_checks_tip`, so a caution row here names its cautions too. The one difference is deliberate: the switch filters the **ranked list only** — `_filter_rows` sets `state["rows"]` from `state["all_rows"]`, while the top-pick cards are built from the chip-filtered signals and are untouched. The summary's count line follows it through `finder_view.only_clear_counts`: *3 of 40 shown · 37 hidden by Only clear*, inserted BEFORE the *Scanned N of M · ‹choice›* part that must stay beside the Change link it explains, and worded *3 of the 20 in the chosen strategies shown* while a strategy chip is active, since `total` is then the chip's list rather than the scan. Nothing hidden adds nothing. **Refresh:** a re-stamp is only ever FLAGGED on the loop (`checks["due"]`) — by the 2 s poll seeing a `REFRESH_VIEWS` version move, by a new answer landing, or by the `TABLE_REFRESH_SEC` timer finding `options:matrix` moved — and a `CHECKS_TICK_SEC` (0.5 s) tick takes it through `run.io_bound`, guarded by a `busy` latch and by `state["gen"]`, which drops a result the page has replaced. Its fresh memo makes a chip click re-stamp nothing (one context, one `_allow_paper` per id), and the memo is emptied whenever either can change. A list painted before the page's first context read is left UNSTAMPED — a dash and hidden by Only clear — rather than stamped against no context, which would print *Partly checked* on every row until the read landed.

## `/options/income`

**Income Window — the 30–45 DTE premium-selling board (NEW 2026-09-05).** A third scan horizon beside 0-DTE and swing, in the Options group's *find* phase beside the other two scanners (`OPTIONS_CHILDREN`, tab colour emerald `#3dd983`). Tier-1 reader of **`cache:options:income`**, published by `handlers.publish_income` from a **once-daily** slot (`[slots.income]` in `config/sessions.toml`, 08:52 CT with a 20-minute grace; the gate is `scheduler.income_slot_due`, mirroring `analyze_slot_due`). Read-only — there is deliberately **no Refresh button on the page**, because the pass is ~23 chain fetches and the whole design premise is call-count minimisation. There is no command that forces a pass either: the `income_scan` command had no producer and was removed 2026-09-19, so the scheduled slot is the only way the board is built. **One board for the WHOLE watchlist**, jointly ranked, unlike `cache:options:swing`, which is one on-demand symbol at a time — a reader picking today's income trade compares across the watchlist rather than within a symbol.

**Four structures on one list:** put credit spread (*Put spread*), call credit spread (*Call spread*), cash-secured put (*Cash-secured put*) and — folded in from the paper account's own share lots — *Covered call*. `side_label` names the position a human trades rather than the engine's structure code, and an unmapped type falls through as its raw identifier rather than being mislabelled as one of the four. Two-sided by construction: `screen_spreads` loops both expiry maps out of the same chain object, so the CCS side costs no extra Schwab call.

**Columns:** Symbol · Side · Strikes · Expiry · DTE · Credit $ · Capital $ · Return on capital · Yield on cost · Total return if called · PoP % · Breakeven · Earnings · Score. Arrives ranked and is **deliberately not re-sorted page-side** — `handlers._income_rank` already pins an absent or NaN score LAST, and duplicating that rule here would contradict it at worst; the headers stay click-sortable.

**⚠ The rows are HETEROGENEOUS and no cell may assume one shape.** An adapted credit spread carries BOTH the flat `short_strike`/`credit`/`rr_pct` contract and the normalized one; the natively-built `SHORT_PUT` carries only the normalized shape (`legs` / `rr` as a ratio, not a percent / `breakevens` as a list, not a scalar). Every cell therefore reads a field both shapes have — `legs` / `net_credit` / `capital` / `max_profit` / `breakevens`. Reaching for `short_strike` would render the two spreads and silently blank the single.

**⚠ Units: `credit` is PER SHARE (0.60), `net_credit` is PER CONTRACT (60.00)**, and the single carries only the latter — so the page reads `net_credit` everywhere. Putting a $0.60 row beside a $640 row on one board is precisely what the per-share field invites. A covered call's dollars are per contract too, with `quantity` carried separately: scaling by lot size would put a 3× row beside 1× rows and make the board incomparable.

**`Capital $` and `Return on capital` are what make the board comparable at all.** Capital is the cash actually committed — a defined-risk spread's max loss, a cash-secured put's full strike-to-zero collateral — so the shapes share one honest column instead of a "Max loss" meaning different things on adjacent rows. Return on capital is `max_profit / capital`; a $60 credit on a $441 spread and a $640 credit on a $39,361 cash-secured put are 13.3% and 1.6%, and dollars alone rank them backwards. Both operands go through the strict `fmt.num`, since a NaN capital would pass a naive guard on the reachable half of the division.

**`Yield on cost` and `Total return if called` are covered-call columns and dash on every other structure**, gated on the **structure** rather than merely on the field reading — a stray value stamped on a spread would render as a return on stock nobody holds. The payload carries **fractions** (0.0168), not percents. ⚠ They are not duplicates of Return on capital and the next reader will think they are: on a covered call the two land within a few hundredths because `capital` **is** the cost basis there, but Return on capital is net of the opening commission, Total return if called is gross, and Return on capital is the only one of the two the other three structures have at all.

**A covered call carries NO `composite_score`, and its Score cell is a dash.** The Fit+Quality scale is calibrated on defined-risk option structures against an inferred market view, and a covered call's economics are dominated by a stock position that scorer never sees; inventing a number so the row sorts higher would be fabricating a reading. `_income_rank` sends an absent score to `-inf`, so these rows land at the **foot** of the merged board — the honest place for a row scored on a different question — and the two ratios above are what a reader ranks them on instead.

**Earnings is a THREE-state stamp, not a boolean** (`shared.earnings.coverage`'s vocabulary, rendered unchanged): *After expiry* (a report is scheduled and falls past this expiration — a statement of fact, since `check_earnings_conflict` already dropped the straddling expirations), *None scheduled* (the calendar covers the symbol and has nothing ahead) and **Not checked** (`not_listed` — the calendar has no entry, so the check did not run). Absence of the stamp renders a dash, never the cleared label. ⚠ **`not_listed` is the COMMON case, not an edge one:** without an Alpha Vantage key at `shared/alphavantage_key.txt` (or `ALPHAVANTAGE_API_KEY`) and a populated `EARNINGS_CALENDAR_DB`, **every row on the board reads "Not checked"** and no expiration is dropped for a report. It is therefore rendered in the NEUTRAL class rather than an amber one — a board painted entirely amber trains the reader to ignore the column — and it means *unknown*, never *clear*. The gate deliberately does **not** fail closed on it: doing so would empty the whole scan on a checkout with no key.

**The status line has THREE states, not two.** `copy.WAITING_OPTIONS` is only for a feed that has published nothing; a pass that ran and found nothing says *"Nothing cleared the 30-45 day income window across N symbols"*, because a once-daily scan of a market with no qualifying premium is healthy and wording it like a cold feed would report that as an outage. `scanned_symbols` is what was **attempted** (so 1 after 22 failures cannot read as a thin market), a failed-symbol count is appended when `errors` is non-empty (a whole-watchlist outage would otherwise look exactly like a quiet tape), and the `ts` is parsed for its own offset — the service stamps this view in **CT**, unlike the matrix view's UTC.

**The one control: an `income_open` per-row button, and it targets the paper ACCOUNT, not the ledger.** A wallet button (`handoff.add_income_row_actions`) sits on `SHORT_PUT` and `COVERED_CALL` rows only, gated on `_allow_open` stamped by `candidate_rows` from `handoff.income_openable` — the two spreads keep their existing ledger route (`paper_create` from the Market Scanner), and the two books are different: the ledger tracks marks, the account holds cash, reserved collateral and `equity_lots`. **This is the only production path into a share lot.** `run_entry_cycle` sizes every candidate off `sig["width"]`, which a single-leg short has none of, so it dies in that function's broad `except`; until `compute.open_income_position` existed, assignment, the Shares page, the covered-call half of this board and the called-away disposal were all reachable only from hand-built test fixtures. `shared/tests/test_cross_tier_mirrors.py` pins the page gate against the service's `INCOME_OPEN_STRUCTURES` — a button on a row the service refuses is a dead control, and a structure the service accepts with no button is a feature nobody can reach.

**The open is priced LIVE, and the board's price is only a sanity check.** `_make_leg_pricer` fetches the contract's own chain and the position is written at that mid; the morning board's `net_credit / 100` is compared against it and a drift past `INCOME_PRICE_DRIFT_TOLERANCE` (**0.15**, deliberately `paper_adjust.apply_adjustment`'s fraction — the Rescue board's Execute already refuses on exactly this rule, and a second tolerance would mean two answers to "has this moved too much") **refuses** and names both numbers. ⚠ `income_price_drift` **rounds to 6 places**: unrounded, `abs(1.70-2.00)/2.00` is 0.15000000000000002 and `abs(2.30-2.00)/2.00` is 0.1499999999999999, so the boundary landed on opposite sides for the same drift depending only on its sign. **The opening credit is not credited to cash** — this book realizes a credit at close through `_close` → `realize_pnl`, exactly as `run_entry_cycle` and `open_driver_position` do; crediting at open would double it at settlement.

**Collateral: the full strike notional for a cash-secured put, and NOTHING for a covered call.** The put's `strike × 100 × qty` is what makes it cash-*secured*, and it is the reservation `_assign_shares` relies on already being back in cash when the put is assigned. A covered call is collateralised by shares the account already holds, which `equity_at_cost` already counts — reserving against them would double-count the same capital, and `reconcile_buying_power` (which recomputes reserved from the OPEN `paper_positions` sum) would hand it straight back at the next service start, so the double-count would also be unstable. It therefore stores `max_loss_total = 0.0`, which is what keeps `_close`'s unconditional `release_buying_power` a no-op on it.

**Five refusals, each with a machine `reason` AND a whole sentence for the reader** (a code alone reaches the user as `insufficient_cash`; a sentence alone cannot be tested for without matching prose): `insufficient_cash` (names the collateral needed and the cash held) · `no_lot` (matched on the row's `lot_id`, so a lot called away since the morning scan refuses rather than silently writing the call against another lot of the same symbol) · `partial_lot` — **a covered call must cover a lot WHOLE**, because `close_equity_lot` disposes of a lot whole, so a partial call could never be delivered against it; the message names the contract count that would work · `already_covered` (per **symbol**, matching what the book can express — `paper_positions` holds no link back to the lot, which is why the Shares page shows one call against every lot of a name; a second open call would report shares as covered once when they are written twice over) · `stale_price` / `no_quote`. Plus `unsupported_structure`, `bad_row`, `bad_quantity`, `halted` and `no_account`.

**Every outcome is published, including a refusal**, to **`cache:options:income_open`** (a SEPARATE view from the board, TTL 600s) and read back by the page as a toast — `open_result_display` maps opened/rejected/error to positive/**warning**/negative, a refusal being a warning because "the account has $8,000 and this needs $10,000" is the system working and painting a rule red trains the reader to read a rule as a fault. A refusal that stayed in the log would leave the reader unable to tell a rule they broke from a service that is down. The payload carries a monotonic `seq` so two byte-identical refusals in a row are two distinguishable publishes. `watch_view` **seeds** that view's version, so navigating back to the page does not re-toast the last outcome as if it had just happened. `income_open` takes `_is_stale_open` — the same replay gate `paper_create` takes, because it MUTATES the book — and only a *successful* open republishes `cache:options:paper_account` (a refusal changed nothing, and repainting three screens to say so would be noise).

## `/options/gamma`

Dealer Positioning (GEX/Charm/DEX/Vanna bars + flip/**single Call+Put walls** + intraday heatmap; **fixed ±20-strike window** around spot for bars+heatmap (`strikes_around`, consistent candle/cell size all day; heatmap cropped to the window; the visible strikes are first resampled onto an EVEN ladder by `gamma.uniform_strike_grid` and `rowsize` is that ladder's step — the median gap alone striped **$NDX**, the one symbol quoting mixed 5/10-wide strikes, see the gotchas); **blended interpolated heatmaps** (intraday **and Term**) — smooth image, no lines, **PLASMA `HEAT_STOPS` colorscale (2026-08-15)**: call-heavy (positive net) runs deep-blue→cyan→ice, put-heavy runs aubergine→magenta→pale-pink, zero still →transparent, over a blue→magenta vertical **wash** on `plotBackgroundColor` (`gamma._wash_background()`, framed by a `PANEL_BORDER` hairline) so quiet strikes read as dim colour instead of empty space. **One `_coloraxis` serves all five views** — GEX/Charm/DEX/Vanna *and* Term — so the palette is a single edit. The two "hot" stops double as `POS_COLOR`/`NEG_COLOR` for the by-strike bars AND as the **sided** wall lines (`CALL_WALL_COLOR` cyan / `PUT_WALL_COLOR` magenta — `WALL_COLOR` is gone), so a bar, a wall and the cells around them read as one instrument; `FLIP_COLOR` moved to the lavender the walls vacated because its old `#42a5f5` now collides with the call ramp, and a test pins flip + proj-flip OUTSIDE the ramp. The spot line keeps its off-white but gains a dark halo via a series `shadow` (NOT a second series — the series count is load-bearing). **The bars are 3D + glowing (2026-08-15), matching the design's rail:** `bevel_fill()` BAKES the design's 5-stop white→black glass wash into the bar's own colour via `_composite()` (an SVG fill takes ONE paint, so it can't be layered like the CSS), and `glow()` is a zero-offset `shadow` in the bar's own colour. **Two probed Highcharts facts drive the shape of this:** a gradient with 0–1 coords leaves `gradientUnits` unset ⇒ `objectBoundingBox`, so each bar is bevelled to its OWN box; and **`shadow` is honoured per SERIES only — a per-POINT shadow is silently dropped** (the point gets no `filter` at all). That second one is why `bar_figure` and `hedge_figure` each **split their data into one series per SIGN** (`Call gamma`/`Put gamma`, `Hedge buy`/`Hedge sell`) rather than colouring points inside one series. **The gradient's frame is the thing to get right, and it cost two wrong attempts — both caught only by rasterising a real bar and reading its pixels, never by a test.** A Highcharts `bar` is a `column` whose SERIES GROUP is transformed (measured live: `translate(72,48) rotate(90 117 685) scale(-1 1)`; the hedge `column` group is a plain `scale(1 1)`). An objectBoundingBox gradient resolves in the point's LOCAL, pre-transform frame, so: (1) local **x is the thickness for BOTH panels** — using y shades along the bar's LENGTH and reads as a fade, not a bevel; and (2) the bar's `scale(-1 1)` **mirrors local x**, so it needs `bevel_fill(..., mirrored=True)` or the white specular stop lands on the bar's BOTTOM edge and it reads as lit from underneath. Verified on a real 366x12 SPY bar — top-to-bottom screen luma 196→175→142→97 (cyan) and 156→121→98→66 (magenta), flat along the length; hedge columns 208→179→134→90 left-to-right, flat top-to-bottom. Bar series are now a FIXED 3 (both signs + an always-emitted, possibly empty `Projected close`) and hedge a fixed 2, closing a latent count swing. EM cone + **candle** up-down stay green/red on purpose: they encode price direction, not gamma sign. Design: [plasma palette](docs/plans/2026-08-15-gamma-plasma-palette-design.md). Also transparent chart bg, no fade, **press-and-hold tooltip** (`_HEAT_PRESS_TOOLTIP_JS`); bar/heatmap **width split grows with session** snapshot count; **flicker-free** in-place Highcharts updates; **symbol is a dropdown** — default `$SPX`, populated from the collected universe (watchlist minus `$VIX`) via `cache:options:gamma_symbols`, **syncs to the cached symbol on build + selecting auto-refreshes + repaints ignore foreign-symbol snapshots** (no revert to `$SPX`); Term shows the **next 5 expirations regardless of cadence** (`_term_chain`) and draws a **1px hairline between each expiry column** (`expiry_separators` → xAxis plotLines at `i+0.5`, `rgba(255,255,255,0.22)`) — interpolation blends Term the same way it blends intraday, but its x axis is DAYS, so the blending smears one expiration's exposure into the next when nothing varies continuously between them; **pre/post-market persistence (2026-07-11)** — the charts show the most-recent-available session 24/7 with NO overnight blanking: the by-strike bars come from the live chain (which returns data off-hours) and the heatmap from `active_session_date` (the PRIOR session premarket, flipping to today once the 08:00 CT collection starts) + `load_date_with_grid`; off-hours `spot=None` degrades gracefully; **RTH-ONLY display window (2026-07-28)** — the heatmap **and** the Flow chart plot only **08:30–15:00 CT** (`compute._rth_bounds`/`_rth_only`) while collection stays 08:00–15:20, and `_display_session_date` shows the PRIOR session during the 08:00–08:30 pre-open gap so they're never blank; the **flip + call/put wall levels run ACROSS the heatmap** as yAxis plotLines (`wall_plot_lines`, always-emitted key so an in-place update can't leave the previous view's lines behind; Spot is deliberately omitted — it's already a moving series); an optional **"Level movement"** switch (`app_settings.gamma_level_tracks`, **off by default**) overlays the intraday MOVEMENT of the flip + walls as **step** lines (`compute._level_track` recomputes walls per snapshot from its own grid — the stored `top_pos/top_neg` columns are a different metric and disagreed on 383/383 live rows; runs before `_crop_gamma_views`; GEX/DEX walls only, Charm/Vanna flip-only; the 3 series are always emitted so the count stays fixed); a dashed amber **"Proj. flip"** level on **all four views** (`compute.gamma_snapshot` → `projected_flip`) — where the DEX curve crosses zero once each strike's OWN 0-DTE charm drift is applied; it is one 0-DTE DELTA level drawn everywhere as a shared reference, and the gap between it and the actual flip IS the hedging drift in price (None whenever the nearest expiry isn't today); a compact signed-column **0-DTE hedge-pressure panel** directly UNDER the heatmap sharing its time categories (`hedge_figure`/`hedge_summary_text` over the snapshot's `hedge_history`, plotted in **$B** and colored by sign in the **plasma** pair since 2026-08-15 — **cyan `HEDGE_BUY_COLOR` = dealers must BUY into the close, magenta `HEDGE_SELL_COLOR` = SELL** (was green/red); its OWN element because pressure is in DOLLARS while the heatmap's y-axis is STRIKE, and hidden wherever the heatmap is); the by-strike bars overlay a **"Projected close"** OUTLINE (transparent fill + amber border, `grouping:False` so it overlays rather than halving the bar width) showing each strike's net after its own drift — an outline, not a ghost bar behind, so it reads whether the projection extends PAST the bar or pulls BACK inside it, and only on strikes that actually hold 0-DTE interest; a collapsed **"How to read the 0-DTE close projection"** expander under the status strip renders `page_help.PROJECTION_HELP_MD` — the SAME text as the nav hover guide, mounted on the page because that tooltip is `pointer-events:none` and Quasar sizes it to the space under its nav item (measured: ~466px of a ~1400px guide), so long-form help parked there alone is unreachable, not merely below the fold; a **Spot** picker (2026-07-28) draws the price overlay as **Line / Candles / OHLC** with a **Bar** picker for the 1/5/15-min bucket (`app_settings.gamma_spot_style` / `gamma_spot_interval`; bar size hidden for the line). Bars are DERIVED from the 1-min spot samples (`ohlc_bars` — open carried from the prior close; **wicks understate the true intra-minute range**) and drawn as CORE `columnrange` + `errorbar` with per-point up/down colors (`candle_points`) because the stock module's candlestick/ohlc series would break this chart's in-place update — see the gotchas. Total heatmap series is **fixed at 9**; a **Flow** view (inserted before Term) is the **Premium Divergence** console panel (redesigned 2026-08-15, see below) over the snapshot's `flow` series — call/put premium as a two-tone ribbon whose CROSSOVER is the read, the spot line white on its own scale, status chips, a **strike ladder** and a readout rail (`flow_panels.divergence_panel`/`flow_summary_text`; premium is mid-based, unsigned, forward-only); a **Net Prem** view (2026-08-05, between Flow and Term; the **Flow Field** console panel since 2026-08-15 — shared-scale lines with **terminus labels** instead of a legend, plus a live **leaderboard** rail) plots **net premium (call$ − put$)** for any combination of **28 symbols** in three groups (Indices & Broad / SPDR Sectors / Mega-caps) — the group tab only FILTERS the checkboxes, the **selection persists across tabs**, each symbol keeps a **fixed colour**, and a **DOLLARS / SKEW %** toggle **in the panel header** (since 2026-08-15; the old `Scale` select is hidden and now only holds the state) rescales the axis because the magnitudes span four orders (live: SPY −$375M beside DIA +$0.1M → −46.6% / +2.5% in skew); reads `cache:options:net_premium` (published on the 1-min GEX branch) and **filters client-side** so toggles are instant, with `net_prem_status_text` telling "not collected yet" apart from "the publisher is failing" (see the 2026-08-05 entry); Explain works per-selected-symbol; **Analyze** calls Claude (forced `submit_analysis` tool) and opens an **infographic** tab — regime + bias gauge, per-index price-level ladder + tiles + **what-if** (rally/sell-off/chop), bottom **"Why is this happening"**; **code-authoritative 1-day Exp. move**; also **auto-runs 4×/day** (premarket / ~18 min after open / midday / close) into per-slot keys with **Auto briefings** buttons + a **History picker** (date + slot dropdown → a report regenerated from the persisted briefing history at `/options/gamma-history`) — see the "Gamma Analyze" section below)
**`?view=` pins one view on the PRIVATE route too (2026-09-08).** `/options/gamma?view=Flow` deep-links a single view; bare is the page exactly as it always was. Added so the marketing gallery's three gamma tiles could differ (`tools/gallery_screens.py`) — the route was parameterless, so all three would have captured the identical default GEX view. ⚠ **A pin also changes what the page draws**, through the same `shows_view_picker` / `may_enqueue` gates the public screens use: no subtab picker, and no Refresh / Explain / Analyze / Briefings / History row. `_resolve_view` is TOTAL — an unknown name falls back to GEX rather than raising — and a pin is deliberately NOT offered for `symbol`, which is interpolated into a Redis key name with no allow-list behind it.


## `/options/simulator`

**One shared position; tabs renamed (2026-09-12, same day).** The Copy to Calculator / Copy to Simulator buttons are gone: both pages publish symbol, strategy, legs (with prices) and the selected expiry to `pages/options/shared_position.py` from their `_capture`, and seed from it on render (their own `_LAST_*` snapshot still supplies page-only inputs). While legs wait for their chain, `pending_legs` is what gets published, never the placeholder template; a landed chain/meta publishes the legs it laid. The Simulator carries share legs through (`carried_shares`, a warning line) and writes them back. A leg moved on the Simulator drops its price (no price column there), so the Calculator prices the new contract; the Calculator refills only UNPRICED arriving legs (`refill_prices(only_missing=True)`). The view tabs are now **Price & Time · Volatility · History** (`TAB_PRICE_TIME` first and default; a saved old tab name falls back to it).

**Every expiration, strikes on demand (2026-09-12, same day).** `sim_fetch` with `lazy: true` lists every expiration and builds the snapshot from the nearest two plus the page's needed expiries — one `fetch_snapshot(from_date=…, to_date=…)` per consecutive run, price history on the first only. An unloaded pill enqueues `sim_fetch_expiry`, which extends the stashed snapshot's contracts; its handler merges that expiry's thinned chain into `cache:options:sim_chain` and writes meta marked `added`, and `_merge_meta` moves the legs without re-seeding.

**The entry panel (2026-09-12) supersedes the controls card described below.** The Simulator mounts the same `entry_panel` as the Calculator (default navy tokens; `strategy_exclude=STOCK_STRATEGIES`) with the leg editor in `layout="table"`, `show_premium=False`, and — new — `delta_for=leg_delta(state["chain"], leg)`. The grid reads **`cache:options:sim_chain`** (`{symbol, chain}`), written by `sim_fetch`'s handler BEFORE `sim_meta` from the SAME `/chains` call (`fetch_snapshot(on_chain=…)` → `thin_calc_chain`), version-polled at 1 s off the loop by `_poll_chain`, and painted only when its symbol matches the one on screen. The "Set all legs to" select and the Load chain button are gone (expiry strip; Enter / REFRESH). A grid click moves the leg on that side and type, or adds a one-contract leg when none matches, with no price (`editor.place_pick`), which fires the usual `sim_run` + `sim_replay`. The six position tiles moved to their own card under the panel. Strikes and expiries for the legs still come from `sim_meta` — the engine can only price contracts in its snapshot.

Simulator (**Replay / What-if / IV-shock as SUBTABS under the main strip** + **Controls+Strategy merged side-by-side in one card** (2026-07-11); **multi-leg strategy builder** — a Strategy dropdown over the shared **editable leg-editor** (`leg_editor.py`) replaces the old single-contract selector — driving all three legacy tabs: **Replay** (re-prices the **netted** position along the underlying's recent path → stacked price, position P/L and 4-Greek panels over a gap-compressed integer x-axis w/ a client-side scrub cursor) + What-if (a **dollar profit/loss payoff from entry**: P/L = position value (×100 contract multiplier) minus the **entry mark** (`whatif_baseline` = value at spot *now*) — so profit caps at the net credit, loss floors at width−credit, **matching the Calculator** — with a green profit fill above / red loss fill below breakeven (area `threshold:0` + `color`/`negativeColor`) + faint Profit/Loss washes + labels; Δt is **elapsed** days from now, per-leg decay → **calendars** correct, theta visible as Δt slides) + IV-shock; **Copy to Calculator** button; **dark-navy dashboard theme** via shared `theme.py`; **persists full UI state across navigation** (symbol/strategy/legs/sliders/active tab) + **auto-refreshes on return** via a single-user module snapshot — `page_state.py`; the **Symbol** field **loads the chain on tab-out (`focusout`) / Enter** (deduped; the button reads **Load chain**, formerly Fetch snapshot) with the same **centered wait overlay** (`overlay.py`) until the meta lands; **compact leg cells** + no "Actions" header (shared `leg_editor`))

**The readouts (2026-09-11).** The controls card is **three columns**: symbol controls · strategy + legs (capped at 440 px) · **six position tiles** (Entry credit/debit · Max profit · Max loss · Breakeven(s) · Delta as shares · Theta per day), which fill the width the capped leg cards used to leave empty, so they cost no height. Every figure comes from the PURE **`pages/options/sim_view.py`**; `simulator.py` is widgets and wiring. The expiry figures solve the **expiration** payoff exactly over `{0} ∪ strikes` plus the net-call slope, and read "—" with the reason on **mixed expiries**, a missing strike or no price — never a zero. ⚠ **Every readout — tiles, What-if chart and line, IV-shock table, Replay — counts a result only when it was priced for the legs on screen**: `sim_run` echoes `symbol`/`legs`/`dt`/`mult`, `sim_replay` echoes `symbol`/`legs`, and `simulator._for_screen` → `sim_view.result_matches` compares them (a pre-upgrade payload without the echo is trusted). Gating only the tiles, as first built, drew the last priced position's chart and verdicts beside the default template after a webgui restart. A Calculator hand-off sets the picker to the strategy the legs carry (`sim_view.template_for`). **Strategy line:** a **Set all legs to** expiry select (`leg_editor.apply_expiry`; reverses the 2026-06-24 decision to leave it off this page), an **Edited** chip when the legs leave the template's shape (type/side in the template's qty RATIO + its near/far expiry pattern; strikes never count), and warnings for a short leg outliving its long leg and for net short calls. **What-if:** the Days slider is **fitted to the legs** (`days_range`: max = the longest leg's fractional time to its 16:00 ET close; quarter-day steps within 3 days) with **Now / Halfway / Expiry** snaps, and a readout line interpolates the drawn curve ("At 386.00 on Sep 28: profit $8,240"; "at expiration" once time reaches the last close, because the whole-day step rounds 7.2 days up to 8). **IV shock is a table**, not a chart, with a one-line verdict. **Replay** adds a **Profit / loss** panel (from `sim_replay`'s new `value`/`pnl`), drops Rho from the panels, and puts real times on a category axis. ⚠ **Units:** IV-shock rows and Replay Greeks are **position units** (×100 × qty) since 2026-09-11, marked `units: "position"`; `sim_view.position_units` scales an unmarked (pre-upgrade) payload. ⚠ **The Replay scrubber was stuck at bars 0–1 until 2026-09-11**: `scrub_slider.max = …` set a Python attribute, while `ui.slider` keeps `max` in `_props`. Write `_props["max"]` + `update()` for any slider whose range changes at runtime (the Days slider does the same). Design: [2026-09-11-simulator-friendlier-ui-design.md](plans/2026-09-11-simulator-friendlier-ui-design.md).

## `/options/expected-move`

Expected Move (candlestick price history (6-mo daily) + forward **ATM-IV expected-move cone** to the option's expiration (green/red dashed, √-time fan) + leg **strike lines** (short solid / long dashed, put/call colored) + axis **crosshair** w/ Date(X)+Price(Y) label boxes; opened in a **new browser tab** via stash-handoff from Scanner/Paper/Captured/Calculator, or standalone. **Expiry + strike are chain-driven DROPDOWNS since 2026-08-12** (was free text, where a typo silently produced "No ATM IV for …"): typing a symbol (tab-out/Enter via the shared `bind_symbol_load`) enqueues a new **`em_chain`** command → **`cache:options:em_chain`** (`compute.em_chain_meta`, today→+90d chain reduced SERVICE-side to `{expirations, strikes{expiry: ladder}, spot}` — measured 10.5 MB raw for a 90-day SPY chain vs **28.8 KB** of ladders, which is why this does NOT publish the raw chain the way the Calculator's `calc_chain` does); the expiry list carries a **DTE suffix** (`2026-08-12  (0d)`) so weeklies stay scannable, and strikes are **deduped across call+put** (put-vs-call is the toggle's job, so the ladder doesn't change under it). Picking an expiry redraws; picking a strike/put-call is a **LOCAL-only** repaint via `expected_move_figure(..., legs=…)` — no round trip, since the strike is only a plotLine. **Also since 2026-08-12 the CURRENT-DAY candle is drawn**: Schwab's `periodType=year&period=1` daily history ends at the PREVIOUS trading day, so `compute.today_candle` synthesizes the forming bar from the RAW quote (`schwab_py_client.get_quotes` — the normalized `get_quote` drops `openPrice`; the normalized client stays as a spot FALLBACK), gated on a trading day at/after the 08:30 CT open (premarket `openPrice` is still the prior session's) and no-op'd if the history ever includes today. Schwab's daily-candle epoch is **midnight CT** (verified live), so `_RTH_START`/`_PROJ_CT_TZ` are reused rather than host-local time. This also fixed the cone, which anchored at `candles[-1][0]` (yesterday) while sized from TODAY's spot and so overshot the expiry by a day. ⚠ after 15:00 CT the bar's close is `lastPrice`, which includes post-market prints, and Schwab's high/low may include extended hours — sub-tick on a 6-month chart, documented not fixed. Three page-state traps are commented in `render()`: `state["drawn_symbol"]` forces a redraw on a symbol switch that KEEPS the same expiry string (a shared monthly makes the `.value` write a no-op, so `on_value_change` never fires and the chart would pair the old symbol's candles with the new symbol's ladder), `state["strike_touched"]` keeps a look-back change from reverting a locally-picked strike while still letting an UNTOUCHED multi-leg handoff resend its own legs, and `state["seeding"]` must wrap `.update()` (not just the `.value=` write) because `ChoiceElement._update_options` re-validates and can re-null the value). **⚠ This page's IV + Expected move DELIBERATELY do not match ThinkorSwim, and the difference was measured, not guessed (2026-08-12, PLTR 2026-10-16, 65 DTE) — do NOT "fix" either number to match ToS without first deciding which definition you want.** TWO independent differences that push OPPOSITE ways: (1) **IV source** — `atm_iv_from_chain` reads the single strike nearest spot, which on an equity smile is its **MINIMUM** (measured: 46.08% at K=165, **45.59% at K=170≈spot**, 49.03% at K=175, 48.79% at K=145), while ToS publishes a per-SERIES IV aggregated across strikes and so necessarily sits above the ATM trough (52.11%). Schwab reports the SAME `volatility` for the ATM call and put, so put/call skew is NOT a factor — that's a dead end, don't re-investigate it. (2) **Move definition** — ours is **1 standard deviation** `S·σ·√(t/365)` (a 68% containment band, the correct basis for a *cone*); ToS's chain-header parenthetical is the **expected ABSOLUTE move**, smaller by exactly **√(2/π) ≈ 0.798**, which is what an ATM straddle prices. Reconciliation: 1σ at our IV = **32.90** (what we show) · 1σ at ToS's IV = 37.61 · abs-move at ToS's IV = **30.01** vs ToS's displayed **30.433** (1.4% off, = spot drift between the two readings). **The trap:** the two differences NEARLY CANCEL here (32.90 vs 30.43, ~8%), which is luck, not calibration — on a symbol with a flatter smile our IV would approach ToS's and our move would then read ~25% LARGER. The same `atm_iv` also sizes the drawn cone, so changing the definition changes the chart, not just the text line. The actual ATM straddle mark was 27.25 (real market price, model-free) if a third reference is ever wanted

**A symbol-only hand-off is valid (2026-09-18).** The Symbol Dossier's link hands
over a symbol with no expiry; the page loads that symbol's expirations and waits for
a pick, where it used to try to draw and toast "Symbol + expiry required." Any
caller handing over a symbol alone gets the same quiet chain load.

## The 2026-09-20 kit migration — Phase 2, the Options tools

The Calculator, the Simulator, the Strategy Finder, Expected Move and Dealer
Positioning went onto `pages/ui_kit.py`, with the three shared widgets they mount
(`entry_panel`, `leg_editor`, `strategy_menu`). **`[calc]` retired** — 33 keys to four
data colours, taking `.calc-v3`, `build_calc_css` and the JetBrains Mono link with it —
which leaves **`[flow]` as the only full page-scoped language in the app**, because its
keys live inside a raw SVG chart fragment.

⚠ **`kit.table` gained `rows_number=`.** The Strategy Finder pages **server-side**: a
510-row answer is ~1.96 MB, so only 50 rows are sent, and `rowsNumber` is what puts
Quasar in server mode. `rows_per_page` alone silently means CLIENT paging, so the kit
now takes both and **raises** if asked for server paging without a page size.

⚠ **Three bugs fixed on the way.** The Simulator's charts never reflowed when they
first appeared (the reflow fired only on a tab change, not on the `set_visibility(True)`
a landing result takes — and a chart that mounts hidden measures 0×0 and stays ~600px
wide forever). Expected Move's chain load and compute raised the same scrim and either
landing hid it. The leg table's remove button came out 24px in a 22px track.

**Dealer Positioning keeps everything that makes it work**, and that is checkable
rather than promised: an AST comparison shows every module-level definition except
`render` byte-identical — `HEAT_STOPS`' transparent zero stop, `interpolation: True`,
the `colorAxis` that must exist at element creation, `_HEAT_PRESS_TOOLTIP_JS`'s three
attachment sites, `_set_chart`'s recreate-on-kind-change, `uniform_strike_grid`, the
constant nine-series count and the 40/60 flex split. `gamma-xhair-row` survives — it is
the ONLY selector `_CROSSHAIR_JS` queries, and dropping it would kill the shared
crosshair silently. `EXPLAIN_CSS` was **deleted**: thirteen lines injected into every
render, reaching nothing.

## The 2026-09-20 kit migration — the nine screens below

Phases 3 & 4 of the UI consistency work put RRG, Sector Rotation, Sector & Industry,
Bull / Bear, Momentum, the Market Regime Console, the Macro Board, the Symbol Dossier
and the Desk on `pages/ui_kit.py`. Each gained one header line (title · `Updated … CT`
from the view's `:ts` key · actions, primary rightmost), one status line of counts, one
loading region and one toast vocabulary, and each lost its own ground, face and neutral
ladder. **Charts, heat ramps, quadrant hues, regime colours, tone dots and macro tile
colours are untouched.**

⚠ **FIVE wait spinners on these pages had never once been seen** — RRG, Sector
Rotation, Sector & Industry, the Regime Console and Momentum each mounted the scrim
INSIDE the container its own repaint clears, so it was deleted on the first paint and
every Refresh since called `show()` on a dead element. Measured on each pre-change page:
*zero* spinners survive a render. On Sector & Industry the repaint also runs on sort,
expand and collapse, so it died on every interaction. `kit.region` keeps the spinner on
`outer` and clears only `content` — Bull / Bear was the one page that already had that
split, and its own comment is where the pattern came from.

Design: [app-ui-consistency](plans/2026-09-19-app-ui-consistency-design.md); plan:
[phases 3 & 4](plans/2026-09-19-app-ui-consistency-phase34-plan.md).

## `/sentiment`

Sentiment — nav group **Trend & Sentiment** since 2026-07-11 (three-column top: a **Market Sentiment ring** + a **Market Trend ring** + the **Signals** tile stack. **Since 2026-08-14 the four semicircular Highcharts gauges are TWO concentric SVG rings**, each carrying **Day / Week / Month** on one dial — `rings.ring_svg` (builder removed 2026-09-19 with the rings), mounted with `ui.html` and updated via `el.content`; the Sentiment ring's arcs are the live composite / 5-session mean / full-history mean (`sentiment_arcs`), the Trend ring's are `derived.trend` / `derived.trend_7d` / `derived.trend_30d_ago` (`trend_arcs`). **A horizon with no usable reading draws its track only + an em-dash** — the thing a needle structurally cannot say; see the ring-graphics section below for why that keys on CONFIDENCE, not key presence. **The Today trend reading's state label + regime badge show the FIVE-STATE (direction × aggression) vocabulary** — short labels **Climbing / Stalling / Circling / Gliding / Diving** (the flight words, 2026-09-10 — each carries a hover sentence from `sentiment.TREND_PICTURE`, on this pill and on the Desk's), badge label+description e.g. "Lack of Bearishness — Lower or flat, but sellers aren't pressing — favor PCS" — and the press-and-hold **TREND DETAIL popup gained a "Why" evidence section** (direction/effort/skew/flow/session/rejection/profile/order-flow/option-flow/aggression lines). The **0–100 arc value is unchanged** (still the direction score); the **structural Week/Month arcs deliberately KEEP the old band vocabulary** (structural read = no aggression axis), so the panel carries both. See the root five-state entry above. / component table; the **Signals column is a 1×4 vertical stack of glowing tiles** (BIAS / SIGNAL / YESTERDAY / CHANGE, each icon + letter-spaced label + neon `text-shadow` value + hairline-and-dot rule + footer descriptor; hovering the BIAS or SIGNAL word shows the composite band it covers, from `sentiment.BAND_WORD_PICTURE`, keyed by tile and word, 2026-09-10), with the service's **velocity + divergence lines restored beneath it**; a **"Market Regime"** expander (2026-07-23) = the blended STRUCTURAL read — committed label + confidence, a **transition line** ("Balanced → Rallying · 60%", hidden when stable), **⚠ SUPERSEDED the same day by the Market Regime Console** (see the CHANGELOG entry): the three-column ring/tile top region and the Market Regime expander described in the rest of this row were REPLACED by a single-screen console — header · Sentiment/Trend/Signals cards · regime block (confidence dial + diagnostic tags + ranked share table + callout strip) · footer, in `webgui/pages/console*.py`, scoped by the `[console]` palette in `config/theme.toml`. **Hover the regime word on the dial (2026-09-10)** and a sentence explains what it means and what tends to work in it — `console_regime.py` hangs `pill_tooltip(dial, regime_mix.regime_picture(name))` on the dial itself, keyed by the SAME `regime_mix.REGIME_PICTURE` table the Desk's regime tile uses, so the two screens can never explain one word two different ways; `shared/tests/test_cross_tier_mirrors.py` pins that every word the sentiment service can print (11, including the direction adornments and "Unclear") has an entry. What survives below it: the Daily Sentiment & Trend intraday graphs, the status bar, Refresh, and the Components / Trend Detail popups. The ranked panel below is now rendered BY the console's share table; the description of its logic still holds. Prior text — the classifier's evidence chips, and — **since 2026-08-14, replacing the percent-stacked area chart** — a **ranked membership panel** (`regime_mix.regime_mix_svg` — the SVG builder was removed 2026-09-19; the console's share table renders `regime_mix.rank_rows` — one inline SVG mounted with `ui.html` + updated via `el.content`, the `rings.py` idiom): one row per regime sorted by current share, each with a bar scaled to **the leader** and a sparkline scaled to **its own** range, a change-since-session-open column, and a footer naming the leader's **margin over the runner-up** plus the session's tightest. The stack was the wrong encoding and the live numbers said so — measured 2026-08-14 over 78 samples, the widest swing all day was 9pp, Breakout sat at exactly 0.000 while holding a fifth of the legend, and percent-stacking then *guarantees* the bands fill the height, so the day's two real events (the lead changing hands out of a **0.2pp** gap; Stressed rising from zero to 7.5pp) were both sub-pixel. The margin is a **new** signal: `unclear` measures evidence strength, not how close the top two are, so at 0.2pp the committed label was very nearly a coin toss and nothing said so. Ranking deliberately gives up the old fixed order's stable reading position — with five rows a lead change is rare and is the most interesting thing that happens, so the ORDER is signal; ties break on `REGIME_ORDER` so identical data cannot jitter. `REGIME_ORDER`/`_LABELS`/`_COLORS` moved to that module (re-exported from `sentiment` for its headline helpers). The panel is **width-capped** (`max-w-[720px]`) because a viewBox scales the TEXT too — uncapped at the full ~1100px content width a 13px label renders at ~22px. Reads `cache:sentiment:regime` + `:regime_history` on their OWN 5-min-cadence version probe; "Waiting for regime…" when nothing is published, "Unclear" when the evidence is genuinely weak — see the root Market Regime entry; collapsed **"Daily Sentiment & Trend"** expander = two value-colorized (green/yellow/red) **2-min intraday graphs** (Daily Market Sentiment 0–10 + Daily Market Trend 0–100), rolling **last 5 trading days**, session gaps collapsed, **recorded going forward** by `sentiment_svc` (RTH-gated) into `SENTIMENT_INTRADAY_DB` → `cache:sentiment:intraday_history` (replaced the old 30-day-history line + rolling-avg/velocity/divergence text) — **expanded by default since 2026-07-12**; bottom status bar; **persists across navigation**; **server-side 120s auto-refresh + bridge publish, tab-independent**. **Since 2026-07-12** the Sector & Industry table, Sector Rotation, and the RRG chart are SEPARATE tabs (below) — this page still reads `cache:sentiment:sectors` only to fill the Components popup's Rotation/Sector-Value cells)

## `/sentiment/bullbear`

> ⚠ **The map is EMPTY for the first ~3 minutes after a `sentiment_svc` restart, and
> that is not a fault.** `scheduler.loop()` awaits the startup
> `handlers.refresh(bus, with_sectors=True)` *before* creating `bb_task`, and that
> refresh takes minutes (measured 2026-08-20 in prod: service up 13:54:21, first
> self-driven bullbear publish ~13:58). Until then `cache:sentiment:bullbear` does
> not exist at all and the page shows its cold-cache state.
>
> Diagnosing this: the key being **absent** looks identical to a published-but-empty
> tree if you read it as `env.payload if env else {}` — check the TTL
> (`-2` = missing) rather than the payload. And a failing tick is invisible at INFO,
> since `_bullbear_publish_loop` logs at `log.debug`; call `handlers.publish_bullbear`
> directly to see the real error.


Bull / Bear Map — **new 2026-08-19**, the third tab of the **Trend & Sentiment** group
(after Market Dashboard and Sentiment) and the target of the Desk's eleven-chip sector
strip; design doc: [bull-bear-map](plans/2026-08-19-bull-bear-map-design.md). Tier-1
reader of ONE view, `cache:sentiment:bullbear`. **The organising idea is that "bullish"
is two facts, not one, and every other rotation screen in this app collapses them.**
Each row shows **Trend** (`raw.trend` — the annualised exponential-regression slope of
log(close) scaled by R², signed, absolute, benchmark-free) and **vs SPY** (`raw.excess`
— excess return against the index, signed, relative) as **separate marks that are never
blended into a score**, plus a live **Today** move. Both cascade axes are FRACTIONS
(`sentiment-dashboard/scoring/momentum`) while `day_pct` is already a percent — the page
scales the first two by 100 in `sentiment_bullbear.as_percent` and the third not at all,
which is the kind of unit mismatch that renders plausibly rather than failing. Their
four combinations are the map: **Rising · Leading** (unambiguous strength), **Rising ·
Lagging** (going up, index going up faster), **Falling · Leading** — ⚠ **the trap: down,
but down less, which is exactly the row a relative-strength-only screen paints as a
buy** (measured on the 2026-08-19 payload: 19 stocks and 1 industry sat there) — and
**Falling · Lagging**. A missing axis yields **No reading**, a fifth bucket that is the
ABSENCE of a quadrant rather than a neutral one; ties go the cautious way (a flat trend
is not rising, a zero excess is not leading). The chip **names both axes**, because one
word is precisely the ambiguity the page exists to remove.

**Participation is a third, independent dimension** — the share of a group's
constituents confirming the move — drawn as a breadth bar BESIDE the quadrant and never
folded into it, because it separates two rows identical on trend alone (2026-08-19:
Energy flat on 0.96 participating, Real Estate rising on 0.23). At or below **one
third** the move is thin and **the bar switches to the down hue** to say so — a
judgement, not a fitted number. Sector and industry rows carry it; **stock rows have
none at all**, and that draws differently from an empty bar: no track means "no
constituents / no reading", an empty track means "nothing confirms". ⚠ `participation`
names TWO quantities in the same payload and the wrong one fails silently —
`row["participation"]` is the 0..1 share this page wants, while
`row["components"]["participation"]` is a within-level **z-score**, signed and
unbounded (both written by `services/sentiment_svc/compute._momentum_score_level`); fed
to `breadth_width` the z-score costs every negative row its bar and mis-draws the rest,
with no exception and no blank render. `bullbear.row_participation` exists to be the one
accessor that knows this.

**There is deliberately NO regime headline, and that is the page's central design
decision.** `/sentiment/sectors` and `/sentiment/rotation` already print opposite
risk-on/risk-off verdicts from quantities that are not commensurable (recorded as a
known issue in [CLAUDE.md](../CLAUDE.md); measured 2026-08-17, `+0.37` rendering
"Risk-on" beside `−1.52` rendering "Risk-off"). This page will not add a third: its
headline is **quadrant counts** — "5 of 11 sectors rising and leading" — arithmetic
about the rows on screen, not interpretation. The payload carries `regime` and neither
`sentiment_bullbear.py` nor the Desk strip ever reads it. `bullbear.headline` returns
**`""`** on an empty payload rather than "0 of 0 sectors rising and leading", which
would state a maximally bearish tape nobody measured — so the page has a real
cold-cache state, substituting a line that names the nightly 16:20 CT cascade into the
same slot and hiding the grid. The count strip below keeps all four quadrants **even at
zero** (an empty trap bucket is itself a reading; drop it and "nothing is falling but
leading" is indistinguishable from "that bucket was not counted") and shows the fifth
**No reading** chip only when something really went unscored.

**Two clocks, deliberately, and they fail separately as well as tick separately.**
`computed_at` / `session_date` date the SCORES (last night's cascade — trend and
relative strength need months of history, so there is no intraday version of them);
`quoted_at` dates the day-moves (now). `quoted_at` is **`None` when the live quote call
raised**, and `services/sentiment_svc/compute.bullbear_view` ships the tree anyway: the
cost is one column, not the page, and the page says so rather than calling itself stale.
A malformed tree by contrast RAISES, because an all-None day-move column would hide it.
Tier 2 merges the nightly cascade (11 sectors, 69 industries, 296 stocks on 2026-08-19)
with **ONE batched `/quotes` call** covering all 374 distinct symbols — verified against
the running proxy that 374 return in a single call — deduped because an industry ETF is
usually a scored stock too. ⚠ `schwab_client.get_quotes` returns a **FLATTENED**
`{symbol: {"change_pct": …}}` mapping (`schwab-proxy/proxy_client.py`), NOT a nested
`{"quote": {…}}` envelope; reading the envelope shape yields `day_pct=None` for every
one of the 374 rows, silently. An omitted symbol leaves `day_pct` None → an em dash,
never `0.0`, which would claim "unchanged" — but the converse does not hold, since
`SchwabProxyClient._extract_change_pct` falls through to a literal `0.0`, so a `0.00%`
cell is no proof of a flat tape. Publish cadence is `scheduler.bullbear_due`: **every
~30 s tick whenever the tape is in ANY open session** (regular, GTH or curb — gated on
`market_calendar.session_at`, NOT on RTH, since extended hours are live and a 15-minute
off-hours gate would leave the Today column that stale through exactly the sessions a
reader most wants current), throttled to **once per 5 minutes on a genuinely closed
tape**, where the quotes are frozen and `cache_set(skip_unchanged=True)` drops the write
anyway — the closed tick buys only the `{key}:ts` freshness stamp.

**The tree expands lazily** — the default screen is eleven sector rows; industries build
on a sector expand and stocks on an industry expand, so all 376 rows are never in the
DOM at once. A body that already has children was filled before (that is the cache), and
every branch adds **at least one** child — a note where there is nothing else — so the
"already built" check can never misread; real states it must draw include an industry
with no admitted member stock (3 of 69) and `orphan_stocks`, constituents whose industry
was never scored (10 of 296), filed under the sector rather than dropped. ⚠ that orphan
mechanism is the ADMISSION GATE, not the four duplicate-ETF industries: `compute.
_momentum_universe` puts those in `orphans` rather than `universe["industries"]`, and
`industry_of` is built only from the latter, so a stock whose sole industry is one of
them resolves to `("", "")` and `build_tree` drops it from every row and every count.
No `group=` on the expansions: accordion behaviour would close one sector as another
opened, and comparing two sectors is the point of the tree. **Two repaint paths for the
two clocks**: a version change carrying only new quotes **reprices the day cells in
place**, since rebuilding would collapse every branch the reader had opened, twice a
minute — only `scores_signature` (the two score stamps plus the sector symbols and axes)
rebuilds, and it resets the day-cell registry first, or later ticks write into elements
no longer on the page. Polling is the cheap `:ver` probe every 2 s. **Refresh** enqueues
`refresh_bullbear` on `cmd:sentiment` and raises a scrim bounded by a **clock**
(`REFRESH_WAIT_SEC`, 8 s) rather than an ack — on a frozen tape `handlers.
publish_bullbear` carries the stored `quoted_at` forward, `skip_unchanged`
short-circuits, and nothing on the bus moves at all, so there is nothing to wait for.
**No Highcharts** (it is a tree, and that also dodges the documented mount-hidden
collapse trap). Pure display arithmetic in `webgui/pages/bullbear.py`
(`tests/test_bullbear.py`, browser-free); `webgui/pages/sentiment_bullbear.py` is
widgets and wiring only. The Desk's sector strip reuses that same module's decisions —
`by_strength` ordering, `quadrant`, `breadth_width`, `signed_pct` — so the strip and the
page it links to cannot list the same sectors differently.

## `/sentiment/sectors`

Sector & Industry Performance — **rebuilt 2026-08-17 as a magnitude-forward heat grid** from a supplied design (README + two screenshots); design doc: [sector-heat-grid](plans/2026-08-17-sector-heat-grid-design.md). Tier-1 reader of `cache:sentiment:sectors`, unchanged. **Day / Week / Month are three adjacent filled tiles flush to the right edge**, so the colour band is continuous across a row and down the page and each figure sits *inside* its tile — magnitude is the primary encoding, not a green/red sign on a plain number. **Intensity is normalised per column** against that column's own spread across sectors **and** all industries (whether or not they are expanded — so opening a sector never repaints the rows above it). **⚠ The scale is the column's 90th percentile, NOT its maximum, and that is a deliberate departure from the reference design**: that prototype normalises on the max, which works on its *synthetic* industry placeholders because they cluster near their sectors, but real industry ETFs have a fat right tail — measured live over the 81-row set, one +27.5% Month reading against a 3.2% median pinned all eleven sectors into 4 of the 13 steps, i.e. destroyed the very property ("a column always uses its full range") the design exists to get. On p90 every column spends all thirteen steps; the handful above it saturate. **Below a per-horizon flat band a cell reads neutral** — ±0.50% Day, ±1.00% Week, ±1.50% Month, widening with the horizon so a quiet month doesn't glow merely because a month drifts further than a day. **The ramp is oklch** (L 0.175→0.300, C 0.022→0.110, hue 158 up / 22 down; flat `oklch(0.155 0.004 90)`), authored in that space because "one step brighter" is one step brighter to the *eye* there, and the grid lives at the dark end where an sRGB interpolation bunches; the figure's colour lifts with its tile's intensity. It is **stepped into 13 static classes, not continuous**, per the Tailwind-first rule on data-driven colour. **66px sector rows** carry a **rank line** under the name (`RANK 1 OF 11 · DAY`) that follows the active sort; industries render the same tiles at **34px** behind a hairline indent rule. **Day / Week / Month headers sort** (click to switch, click again to reverse; default Day descending) and the rank line restates itself accordingly. **P/C stays a plain number** tinted amber above 1.5 — it is a ratio, not a percentage change, so a heat tile would invite reading it as a fourth timeframe. **The RRG quadrant column was dropped**: `/sentiment/rrg` and `/sentiment/rotation` show that read properly, and a one-word quadrant beside a colour band had the same misreading problem. Header carries an eyebrow stamp (`MARKET STRUCTURE · AUG 17, 2026` — the market's DATE, still Eastern; `AWAITING DATA` rather than an invented "now" on a cold cache). ⚠ **The clock left that line on 2026-09-20**: it rendered Eastern where every other stamp in the app is Central, so the time moved to the kit header's `Updated … CT` and the eyebrow kept only the date and a regime line (dot + headline + `cyclicals -0.41% vs defensives -0.66%`, on the thresholds the deleted `sentiment.rotation_banner` used. ⚠ **This line can contradict the Sector Rotation tab, and often does** — it reads `sector.rotation.day_spread` (cyclical minus defensive daily *% return*, bands ±0.3/±1.0) while that page reads `assessment.headline.spread` (mean *RS-momentum* spread, threshold ±1.5). Measured live 2026-08-17: **+0.37 → "Risk-on regime" here, −1.52 → "Risk-off" there.** Pre-existing — the old table did the same — and recorded as a known issue in CLAUDE.md). ⚠ **Typography was Instrument Sans + JetBrains Mono from `[sectors].font_url` until 2026-09-20**; the page now wears the app face, and `tabular-nums` on IBM Plex Sans holds the columns (measured: 0.000 digit-width spread). `[sectors]` keeps only `up`/`dn`/`warn`. The grid sits in an `overflow-x-auto` wrapper at `min-w-[860px]` so the band never tears away from its row. Pure transforms in `webgui/pages/sector_heat.py` (`tests/test_sector_heat.py`); the page is widgets + wiring only, and **needs no `ui.add_css` block at all**

## `/sentiment/rotation`

Sector Rotation — **rebuilt 2026-08-17** from a supplied design (`Sector Rotation.dc.html`); design doc: [sector-rotation-board](plans/2026-08-17-sector-rotation-board-design.md). A **pure Tier-1 re-render**: `cache:sentiment:rotation` already carried every field, so `sentiment_svc` was not touched. Three regions replace the old headline-plus-table. **A verdict strip** of three hairline-ruled panels: the regime word with a tone dot and a plain sentence; the cyclical-vs-defensive means either side of a **diverging spread gauge** — a −3…+3 track with both ±threshold triggers ticked, zero marked, and a fill spanning *between the reading and zero* rather than growing from one end (the quantity is signed, so which side of zero it sits on **is** the verdict; a left-anchored bar would encode −3 and +3 as "small" and "large" instead of "opposite"); and the spread itself over a derived sentence saying how far past its trigger it sits. **A flow band**: one segment per rotating sector, `flex-grow` carrying its S&P weight so segment area *is* index share, with the two sides' wrappers also grown by their totals — so the halves are to scale against each other, not merely internally. The split keys on the engine's own `direction` field (not the quadrant), so the band partitions exactly what the assessment called rotating and the two totals sum to the share of the index in motion. A segment under **7.5% of its own side** drops its label rather than clipping it. **Four quadrant panels** (Improving · Leading · Lagging · Weakening — rotation reading order, not alphabetical), each with its share of the index, a fixed blurb, and a chip per sector carrying RS-Momentum and a weight bar; **all bars share ONE scale, the heaviest sector on the page**, so a 2% sector alone in a quadrant cannot draw the same bar as a 32% one. Every panel renders even when empty — a quadrant nobody is in is information, and dropping it would silently reflow the other three. **Retired:** the Full Quadrant Map table (and with it the `RS-Ratio` and `Dir` columns) and the ROTATING FROM/INTO name lists — the band and the panels carry that content, and the band adds the weight the table never showed. The `pairs` field is unused; the "pairing is ordinal" footnote survives. ⚠ **The quadrant palette is page-scoped and deliberately differs from the RRG tab's** — see the CLAUDE.md note. Pure builders in `webgui/pages/rotation_view.py` (`tests/test_rotation_view.py`); the RRG figure builders stay in `sentiment_rotation.py` because `pages/sentiment_rrg.py` imports them. **No `ui.add_css`** — the gauge is absolutely-positioned runtime percentage arbitraries (`left-[…%]`/`w-[…%]`), the documented continuous-value exception

## `/sentiment/rrg`

RRG — **rebuilt 2026-08-17** from a supplied design (`RRG.html`), the third screen from that design project; design doc: [rrg-plot](plans/2026-08-17-rrg-plot-design.md). The Highcharts spline scatter is replaced by a **hand-drawn plot**: absolutely-positioned markers over an SVG trail layer, on four quadrant washes with a fixed crosshair. Highcharts brought a scale model, a legend, a tooltip engine and a reflow lifecycle this needs none of, against the app's documented list of Highcharts traps; eleven markers and forty-four line segments do not justify any of it. Still a Tier-1 reader of `cache:sentiment:rotation` — no service change. **Marker AREA is the sector's S&P weight** (diameter goes as √weight, so a linear-diameter encoding would show Technology as ~10× Utilities rather than the ~16× in area it is), and **each trail is the engine's last five `tail` readings**, fading *and* thinning toward the past — age encoded twice, because either alone is ambiguous against eleven overlapping trails, and together they read as direction without an arrowhead. Three departures from the supplied design, each forced by real data: **(1) the domain is computed, not fixed** — the design hard-codes RS-Ratio 98.9…101.1, and real five-reading tails reach **97.28**, which that window would clip clean off the plot; it is derived from the data, padded 8%, floored at the design's window so a becalmed session cannot zoom into noise, and **kept symmetric about 100**, which is load-bearing because the washes and crosshair are drawn at exactly 50%/50% and an asymmetric window would put the axes somewhere other than 100/100 and silently reassign every sector's quadrant on screen. **(2) The trails are real** — the design generates a plausible spiral with `sampleTail()` and says so in its own footer. **(3) No `vector-effect`** — the design scales a 0–100 viewBox with `preserveAspectRatio="none"` and relies on `vector-effect:non-scaling-stroke` to stop the stroke stretching with it; **that attribute is NOT in DOMPurify's allowlist**, so `ui.html` strips it and every trail renders thick horizontally and hairline vertically. Percentage coordinates on the `<line>`s need neither the viewBox nor the rescue, and leave `stroke-width` in real pixels (guarded by `test_tail_svg_emits_nothing_dompurify_would_strip`, the same invariant `rings.py` carries). **Trails are smoothed** (2026-08-17): each is resampled along a Catmull-Rom spline at `SMOOTH_SAMPLES`=6 sub-segments per span, so a 5-reading trail draws 24 sub-segments instead of 4 straight ones and the width/opacity taper becomes CONTINUOUS rather than stepping once per reading. Measured live, the per-segment turn angle within one trail fell from **max 142° / median 27.3°** to **max 8.3° / median 4.0°**. The curve passes through every real reading — smoothing only chooses the route *between* them — the end tangents are clamped so a trail cannot flare past its own endpoints, and `TAIL_TENSION` is held at the standard 0.5 with a bounding-box test, because a spline that overshoots is claiming the sector visited a position it never held. Cost: 264 `<line>`s / ~34 KB of SVG for eleven sectors, on a page that repaints only on a manual refresh. **Markers are labelled with the SECTOR NAME**, not the ETF code, in the sans face — a proper noun set in a mono ticker face reads as a code. Because a name is ~4× the width of a ticker, the side decision **measures the label** (`label_width_px`) against the plot's right edge rather than using a fixed threshold: "Communication" placed right of a marker at 70% would hang off the plot where "XLC" fitted. Labels are then **decluttered per side** — eleven sectors cluster hard around 100, and without it they overprint into a smear. A tinted verdict strip above the plot carries the regime, its sentence and the arithmetic. Pure builders in `webgui/pages/rrg_view.py` (`tests/test_rrg_view.py`), sharing the quadrant palette with `rotation_view.py` so the two tabs cannot drift (⚠ the **warm-neutral ladder** they also shared was retired 2026-09-20 with `[rotation]`; both now take the app's surface and text tokens). The Highcharts builders this replaced (`rrg_scatter_figure`, `_sector_trace`, `quadrant_label_bands`, `_hex_to_rgba`) and the old local quadrant palette were **deleted 2026-08-17**

## `/sentiment/momentum`

Momentum (NEW tab 2026-07-28, `pages.sentiment_momentum`, last tab in the Trend & Sentiment group): the **momentum cascade** — a regime-conditioned momentum score across **3 levels** (11 sectors, **70** industry ETFs, 311 stocks from the workbook's new **Stocks** tab). **Recomputed ONCE NIGHTLY** (`sentiment_svc scheduler.momentum_due`, 16:20 CT weekdays) — daily bars change once a day, so ~390 regressions on the 120 s tick would be waste. Tier-1 reader of **`cache:sentiment:momentum`** (`MomentumSnapshot`): a **regime banner** (favorable / neutral / **suppressed** = momentum-crash risk, plus the lookback that state implies — in `suppressed` the banner is the loud element and the leaderboard dims), a **quadrant scatter** (score x, acceleration y, series per sector; Leading / Improving / Weakening / Lagging — deliberately the SAME four names as the RRG tab, since both are 2x2 strength-vs-rate-of-change scatters in one nav group), a **rank ribbon** over recent sessions, and a **top/bottom-15 leaderboard** showing every component column + a 3-block sector/industry/stock **alignment** flag. Footer counts `excluded` symbols (liquidity / insufficient_bars / no_quote / duplicate_etf) with a hover listing them — how a renamed or delisted ticker becomes visible instead of silently vanishing. **NOT a sentiment component**: `scoring/__init__.WEIGHTS` and the bridge are untouched, by design


## `/sentiment/momentum` — 2026-08-17 rebuild

**Rebuilt** from a supplied design (`Momentum.html`), the fourth screen from that project; design doc: [momentum-guided-page](plans/2026-08-17-momentum-guided-page-design.md). Still a Tier-1 reader of `cache:sentiment:momentum` — **no service change**, the design binds to the payload as it already exists. The page becomes a **numbered argument** rather than a dashboard: **(1)** *Is momentum worth trading today?* — all three regime states side by side with the live one enlarged and each carrying an instruction line, then a dispersion strip (percentile + 0–100 bar + the service's own reason). Showing all three states at once is the point: the old banner named the live state and left the reader to remember what the other two would have meant, when the whole premise of the page is that momentum is only tradeable in some conditions. **(2)** *Three levels, and where they agree* — each universe's top-quartile count on a track whose width scales with **√size** (linear would make the stock track 27× the sector track and squash the smaller two to slivers), plus a headline count of stocks where industry **and** sector both confirm — **and, since 2026-08-18, the names behind that count**, ranked, as clickable chips (`aligned_names`). The count says how many tell one story; the value of the panel is *which*, because that is the list you take to Trade Analyzer, and a number with no membership was unactionable. `alignment_count` is now `len()` of that list, so the figure above the chips and the chips themselves come out of one filter. An aligned row exists only at the **stock** level, so picking one **switches the level selector** rather than stranding section 4 on a silent fallback; chips carry a `sector · industry` tooltip, since a ticker alone does not say what it is. **(3)** *Where the names sit* — the four quadrants as counts, shares and the strongest names, replacing the Highcharts scatter; all four render even when empty. **(4)** *What a score is made of* — the **top-ranked** row decomposed into diverging z-score bars (centred, because the sign is the reading; clamped at ±3 where the service caps them). Deterministic on purpose, so the anatomy card and the leaderboard's first row are always the same name. **(5)** *Rank over recent sessions* — a hand-built SVG replacing the ribbon. Then the **limits cards**, which state on the page the four ways this screen can be confidently wrong. **The ranked leaderboard survives behind a COLLAPSED expander** — and **sorts** from 2026-09-20, which it never did before (the kit's table makes every data column sortable; the rows still arrive pre-ranked top-15 / bottom-15) — deleting it (as the design does) turns a screener into an orientation page with nowhere to see which names to act on; leaving it open makes the argument above it read as preamble. ⚠ **Three design assumptions that would have shipped broken**, each caught against the live payload: **(a)** `rank_history` is **ragged** — symbols carry 15/10/7/**5** sessions, and the design's `i/(len-1)*100` would stretch a five-session symbol across the full width as a full-length trend; every series now shares ONE date axis (verified: AWAY has 10 of 15 and starts at x=35.7%). **(b)** The rank domain is **computed, not capped at 21** — live ranks reach 61 (industries) and 272 (stocks), and the most interesting name is usually the one climbing from deep: today GDX 60th→23rd and TER 272nd→52nd, both cut off entirely by a 21-deep window; the bottom tick is always labelled so a 1…272 axis cannot read as ending at 205. **(c)** **`vector-effect` is stripped by DOMPurify** (verified) — the design's `<polyline>` in a scaled viewBox relies on it, so the lines would render stretched; `points` cannot take percentages either, hence percentage-addressed `<line>`s. Pure builders in `webgui/pages/momentum_view.py` (`tests/test_momentum_view.py`); the leaderboard's own transforms stay in the page module. The Highcharts builders this replaced (`quadrant_figure`, `ribbon_figure`, `ribbon_subset`, `quadrant_label_bands`, `_zero_line`, `banner_parts`) were **deleted 2026-08-17**

## `/trade`

Trade Analyzer (nav label since 2026-07-11; on-demand single-symbol analysis: **Position (1–8wk)** + **Investor (months+)** Buy/Hold/Sell verdicts w/ score + top reasons + hard gates + expandable factor breakdown. The **Position** verdict is now a **backtested, IC-weighted cross-sectional factor model** (`swing_model.json` artifact → live `swing_model.py` scorer): the headline is the **validated** BUY/SELL/HOLD off a **calibration band** + an outcome line (percentile · expected fwd return / horizon · beat-SPY hit-rate) + a **"Why — validated factors"** evidence expander (per-factor z/weight/contribution/IC + model version & OOS IC), with the **legacy heuristic** verdict tucked into a collapsed expander (Investor unchanged); **MTF EMA alignment** (per-timeframe); momentum strip (RSI/ADX/MACD/VWAP/RelVol); sector strength; **Fundamentals card** (P/E/PEG/growth/ROE/margins via proxy `/instruments`); ~~Markov Forecast card~~ — **REMOVED from the page (2026-06)**: it forecast the LEGACY composite score and so contradicted the validated Position read beside it. `webgui/pages/trade.py` renders no Markov card; the `markov{}` field survives in the `TradeAnalysis` contract and `cache:trade:markov_prior` is still published, so this is a UI removal, not an engine removal; **dark-navy "dashboard" theme** (`.calc-v2` via shared `theme.py`, `items-start` compact cards); **tab-out (`focusout`) = Analyze** (deduped); **persists last analyzed symbol** + analysis across nav. **Deep Dive + AI Query buttons (2026-08-04)** run the migrated **EquityDeepDive** engine (`services/trade_svc/deepdive/`) for the current symbol: **Deep Dive** opens a self-contained HTML report (technicals + fundamentals/short-interest + options analytics [ATM IV, implied move, max pain, 25Δ skew, IV term structure, cm30 IV, net GEX/flip, OI walls] + IV/RV rank) in a new tab via `/trade/deepdive`; **AI Query** opens a copyable chat-prompt (digest injected, no API call) via `/trade/deepdive-query`. On-demand IV history (`repo_paths.IV_HISTORY_DB`), IV rank "building" until snapshots accrue; `ai_analyst.py` NOT migrated — see the 2026-08-04 Last-updated entry)

## `/driver`

Claude Trades (nav label since 2026-07-11; **autonomous monitor + override** [level B]: a **Claude decision layer** (Opus 4.8 default; `DRIVER_MODEL` env / `shared/driver_model.txt` override → e.g. Sonnet 5) auto-selects/sizes **defined-risk option spreads (PCS/CCS/IC) from the scanner** (`cache:options:scan`) toward a **ratcheted daily target** (base $500, `TARGET_FLOOR` $250 - `TARGET_CAP` $1,000 against the MTD pace) in **paper**, gated by a **`cache:driver:control`** master switch + confirm-gated **STOP** kill-switch; the page shows day-P&L-vs-$500 progress, open-driver-positions, a newest-first **decision-log** audit (`cache:driver:autonomous`, times in **CST**), and a **Performance scorecard** (win-rate / profit-factor / avg win-loss / P&L by symbol & strategy — `cache:options:driver_paper_perf`), all reading the Driver's **own isolated paper book** (`cache:options:driver_paper_account`, separate from the manual account), with **Enable/Disable** + **Run now**; 09:28-ET morning + 30-min autonomous **entry-window** checkpoints (**09:45–15:30 ET** — the open's first ~15 min skipped so the post-open structure is readable, and **no NEW entries in the last 30 min before the close**; management/exits are unaffected, on options_svc's separate 5-min manage cycle) run `build_packet`→`decider.decide`→**`guardrails.apply_guardrails`** (PURE code clamps size + halts at banked-$500/loss-cap/VIX — the model never sizes its own risk)→`cmd:options` **`driver_paper_create`** (opens into the dedicated `paper_account_driver.db`, repriced + auto-exited on the **1-min** manage tick (raised from 5-min 2026-07-16 so stops react within the minute and the -$1,500 loss-halt read stays fresh) — fully separate from the user's manual paper trades). A **Performance** view shows the driver's **closed trades + realized P&L** from its isolated paper account (`cache:options:driver_paper_account['closed_positions']` — reader-friendly columns Closed/Symbol/Strategy/Qty/Exit-reason/Realized-P&L, colored, newest-first, updated every 1-min manage cycle + the 2s version-poll; a **Refresh** button forces a `driver_paper_manage` reprice). **The legacy morning-agent order-approval queue + its `claude-driver` engine were REMOVED (2026-07-08)** — the page is now purely the autonomous monitor + this Performance view. Orders simulated (`PAPER_TRADE=True`). **Root-cause fix (2026-06-27): the driver had NEVER opened a position** — `compute.open_driver_position` read `signal_id`/`strategy`/`entry_credit` but the driver feeds RAW scanner signals keyed `id`/`type`/`credit`, so every open `KeyError`'d on `'signal_id'` and the defensive `try/except` swallowed it to `status=error`; the decision log showed "executed" (only the ENQUEUE) while the account stayed empty. Fixed by normalizing the signal shape — open positions now appear + the scorecard P&L populates. See [[driver-feeds-raw-scanner-signal-shape]]. **Second root-cause fix (2026-07-02): $SPX/MU logged "Executed" but never opened** — a **100× units mismatch**: `guardrails.clamp_quantity` sized affordability off the scanner's **PER-SHARE** `max_loss` (~$7) while the paper account's `size_contracts` correctly used **per-CONTRACT** dollars (`(width−credit)×100`, ~$705), so the driver kept proposing $SPX/MU whose real per-contract risk ($409–$1,833) exceeded the paper sizer's $250 cap → `RISK_TOO_HIGH` → **silently rejected** (the "Executed" in the log is only the ENQUEUE; the true outcome is in the account view's `last_open_results`, cap 25). Fixed: the guardrail evaluates **per-contract dollars** (`CONTRACT_MULTIPLIER`); the driver's caps raised to **$1,500/$4,500** and the paper open path given its own **`_DRIVER_MAX_RISK_PER_TRADE=$1,500`** (manual account unchanged at $250) — $SPX/MU now open. See [[driver-executed-but-rejected-risk-too-high]]. **Market-context block (2026-07-08):** the decider's packet now carries an additive **`market_read`** — per-index gamma **flip/walls/what-if** from the freshest `gamma_analyze` briefing + a **live spot** (spot-vs-flip **posture**), dashboard **breadth + risk-on/off**, and the **sentiment 0-10 score** — as **reasoning context only** (never filters the menu; `guardrails.py` untouched — the wall-aware gate is deferred). Its one-line summary shows on each decision-log row)

## `/settings`

**Three sub-tabs since 2026-09-19** (mounted in `shell.subtab_slot()`, breadcrumb
bound): **General** — everything below — **Appearance** (`pages/appearance.py`, the
theme editor lifted out of General) and **Configuration**
(`pages/config_editor.py`). Configuration draws every `config/*.toml` setting from
the `webgui/config_schema.py` catalogue, grouped by purpose (Trade selection · Exits
& trade management · Autonomous driver · Flow alerts · Market hours & schedules ·
Symbols & watchlists · Sector map · Commissions, plus Ports and Environments
read-only): plain-English label and help per key, unit suffixes, fractions typed as
percents, inline validation that says why a value is refused, cross-field checks on
Save, a per-key **Shipped: X** chip with a reset button, a per-category reset, a
search box across every setting, and a sticky Save / Discard bar. Save writes only
the differences to `config/local/<name>.toml` (`config_store`, never the tracked
file), logs each change to `config/local/changes.jsonl` (the "Recent changes"
panel), and opens a restart dialog listing the units the changed keys need
(`config_schema.restart_for`; `timers` means `generate_units --install`), warning
during market hours. `test_config_schema.py` fails on any TOML key with no catalogue
entry and round-trips every shipped value through its own field.

General — the original page. Settings (GUI prefs via `app_settings`: scanner **audio alert** on/off + sound + volume, only-during-market-hours, min-score-to-alert; desktop-notification toggle + permission grant + Test sound; ticker toggle/speed (the theme editor moved out to the Appearance sub-tab on 2026-09-19); **API usage** (2026-07-13) — outbound Schwab API-call counts Today / last 7 / last 30 days, read off-thread from the proxy's `GET /stats/api_calls`, **plus Claude (Anthropic) call counts** from the cross-tier `shared/anthropic_counter.py` store (`shared/data/anthropic_call_counts.db`, WAL — recorded immediately before every `messages.create` at the three call sites: driver decider / Gamma Analyze / market-ticker summary; services need a restart to start counting) (counted per actual HTTP request at the marketdata rate-limit chokepoint + the trader loop → per-day rows in `schwab-proxy/data/api_call_counts.db`, forward-only; requires a proxy restart to start counting); **Maintenance** (2026-07-13) — a confirm-gated **Vacuum GEX history DB** button (optional purge-first switch) that runs `tools/vacuum_gex.py` as a subprocess off-thread and prints the before→after size — the tool still refuses while the collector is active)

Appearance (`pages/appearance.py`, 2026-09-19) — every colour and font in **eight
groups that follow the app's design standard, not `config/theme.toml`'s sections**:
Surfaces · Text · Fields · Buttons · Status colours · Charts · Type · Menu. A group
is a DISPLAY mapping over existing keys (`appearance.GROUPS`), so the TOML never has
to move for the screen to make sense, and `test_appearance.py` pins that every key of
`EDITABLE_SECTIONS` appears exactly once — a new theme key cannot ship without a place
here. Colour swatches / text fields / menu rows, `theme.knob_label` labels. Editors
left, a **live preview** right that draws the kit's own pieces from the EDITED tokens
(the saved values with the unsaved ones laid over), which is the only place an unsaved
colour can be seen — the theme loads once at startup. ⚠ **Three groups say so under
their own heading** (`GROUP_NOTES`): Charts colours are Highcharts option dicts, the
Menu is the shell around this page, and only two of Type's eight knobs are drawn — a
group that later grows a preview drops its entry. A sticky footer carries **Reset to
shipped values** (confirm-gated, `theme.reset_theme` drops the override), the unsaved
count, **Discard** and **Save changes** (`theme.save_theme_values` → the
`config/local/theme.toml` override, never the tracked file; a failed write is reported
rather than swallowed), and the shared pending-restart banner offers **Restart now**
(the Status page's windowless self-restart).

## `/eod` · `/eod/detail`

EOD Report (pure-webgui aggregator over `options:*` + `driver:*` caches. **Summary** = headline tiles + a **verbose Daily / Weekly(WTD) / MTD performance** block **per book** — the manual paper **ledger** (`options:paper_trades`) and the **Driver** account (`options:driver_paper_account`, incl. its new `closed_positions`) shown separately (realized P&L bucketed by **exit** date; opened/credit by **entry** date; a per-book now-line = equity/session-P&L/open-unrealized/open-count). **Detailed** = the same performance + **trade-type breakdowns** (by **strategy** PCS/CCS/IC, by **0-DTE/Swing**, by **status** Open/Closed/Expired) for each book + full trade/scanner/captured/driver tables. **Navigation**: a jump-link **TOC** + every section in a native **`<details>`** (collapsible, **no JS** — works in-app AND in the exported files). **Generate** snapshots the caches → standalone `summary.html` + `detail.html` archived under `webgui/data/eod/<date>/`; `/eod/file` serves them raw. **Since 2026-09-17 it ALSO runs itself at 15:15 CT on every trading day** — `trading-<env>-eod-report.timer` → `tools/generate_eod_report.py`, which imports these same builders (a Tier-2 slot could not: the builders are Tier-1, and the webgui's only timers are per-client `ui.timer`s, so an in-app schedule would mean "15:15 if a tab happens to be open"). ⚠ It **writes nothing and exits non-zero** when every cache read was empty — every builder here degrades to a "No data" note, and the archive overwrites per DATE, so an unattended run against a stopped stack would replace the day's real report with a complete-looking empty one. No `Persistent=` on the timer: the report is named for the day it is generated ON, from LIVE caches, so a catch-up run cannot recover the missed day — it would write the NEXT day's date off pre-open caches. Pure builders (`normalize_trades`/`period_buckets`/`breakdown_rows`/`performance_table_html`/`breakdown_table_html`/`toc`/`details_section`) unit-tested. Realized reads `$0`/`—` until trades close — by design, not a bug)

## `/market`

Market Dashboard — **"Macro Board" visual redesign (2026-08-15, presentation-only; see CHANGELOG)**: page-scoped `[macro]` theme section (like `[console]`), notched clip-path panels + tiles, per-category accent bars, magnitude-scaled heat wash, a top rail (**breadth meter** / A-B **skin toggle** persisted in `app_settings.macro_skin`). ⚠ **The wordmark, the "STREAMING" dot and the clock went on 2026-09-20** — the dot was a static claim the page could not back, and the clock rendered naive machine-local `%H:%M:%S`, the only clock in the app that was neither Central nor a data stamp; the kit header's `Updated … CT` replaced both, and **flash-on-change** (ignition bar + price flare fired only on tiles whose value moved — server-side `tile_signature` diff + a batched JS reflow-retrigger). Two skins: A Instrument (default) / B Heat Lattice. Direction/flash/wash colour keys on the polarity-aware `color_state` (NOT raw pct), wash magnitude on `|%change|`. The ONE `ui.add_css` block (`theme.MACRO_CSS`) carries only the **data effects** — the ignition bar, the price flare, the Skin-A wash, the Skin-B heat fill and its bloom, and the Skin-B legibility ramp — plus `prefers-reduced-motion`; everything else Tailwind. ⚠ **The notches, accent bars and radial ground went 2026-09-20**, but the `.macro-board` / `.macro-a|b` wrapper classes STAY on the kit page column: every data rule is scoped under them and dropping them as "chrome" would kill the effects with them. Data/grouping/cadence unchanged. (3-tier, `services/market_svc` :8215: a live grid of ~48 macro tickers from `symbol_categories.csv`, grouped into a **framed panel per category** laid out macro→tape→rotation (Volatility/Options-Sentiment/Internals/Currency · Cash-Index/Futures/Broad-ETF/**Top 10** · Sector/Thematic/Factor/Fixed-Income/Crypto/Countries). Each **tile** shows symbol + description (hover tooltip) + last + net/%-change on a **semantic risk-on/off colored background** (green risk-on / red risk-off / grey no-data, intensity by magnitude) — **polarity-aware** (VIX/SKEW/put-call/TLT/UUP shade RED on up-moves). The **Top 10** frame (renamed from "Magnificent 7" on 2026-07-21) leads with a **composite `BIG10` tile** = the equal-weighted avg day %-move of its **10 members** (NVDA/MSFT/GOOGL/AMZN/META/AAPL/TSLA + AVGO/PLTR/AMD) + a breadth subline (e.g. "8/10 up"), colored by the avg (a `kind="basket"` tile whose members are also its 10 constituent tiles). **Per-symbol premium sublines (2026-07-21):** the SPX/NDX, SPY/DIA/QQQ/IWM and Top-10 tiles carry a small **call/put PREMIUM skew** line ("Call 37%"/"Put 11%", from `cache:options:matrix` rows' `call_prem`/`put_prem`), the BIG10 tile shows the **dollar-weighted net of its 10**, and **every tile is a fixed `min-h-[92px]` so a frame's tiles are all the same height** whether or not they have the subline. `market_svc` polls the proxy's raw `/quotes` on a **~2 s RTH cadence** (5 s off-hours — futures trade ~24h so off-hours stays snappy), normalizes change across INDEX/EQUITY/FUTURE, computes the `$ADVN-$DECN` breadth spread + the `MAG7` basket, and reads the app's own cap-weighted put/call from `cache:sentiment:composite` **+ the dollar-weighted call/put PREMIUM skew ("Net Prem" tile) from `cache:options:matrix`→`premium`** (added 2026-07-21; "Call 46%"/"Put 22%" + a net-$ subline, a money-weighted P/C over the ~45 collected symbols, NOT net buying) → publishes `cache:market:dashboard`; the page version-polls + **updates tiles in place** (no per-tick rebuild). **Five frames are LEADERBOARDS** (four from 2026-08-05; **Broad-Market ETF** joined **2026-08-19**) — **Broad-Market ETF**, **Top 10**, **Sector SPDR**, **Thematic / Industry ETF** and **Countries** are emitted **ranked descending by day %-move** (`symbols.SORTED_CATEGORIES` + the pure `compute.rank_tiles`), with the **BIG10 composite PINNED leftmost** (it carries its members' average as its own `change_pct`, so it would otherwise sort into the middle of them) and no-data tiles last; **every other frame keeps its curated symbol-map order by design** (Volatility's VIX-then-tenors, Cash Index pairing with Futures — that layout IS the information). The rail's **advance/decline meter counts only those four equity frames** (`market.BREADTH_CATEGORIES` — Broad-Market ETF · Top 10 · Sector SPDR · Thematic / Industry ETF), skipping the BIG10 basket so its ten constituents aren't double-counted; over the whole board a bid VIX, a stronger dollar and a rallying Treasury all counted as *declines*, cancelling the equity selling out on exactly the sessions the meter should read hardest. The page mirrors the rank as a Tailwind flex **`order-N`** class (`market.order_class`), swapping the **tracked-previous** class in place, so a re-rank is one class swap and never rebuilds the board. **CSV→Schwab symbol map** handles the translations (`SPX`→`$SPX`, `VIX`→`$VIX`, `/ES[U26]`→`/ESU26`) + **equivalents for symbols Schwab can't quote** (`$DXY`→`UUP`; `$PCALL`/`$PCSP`→the sentiment cap-weighted P/C tile). See the "Market Dashboard" section below)

## Public live screens (`live.neuralstrike.co`) — 2026-09-07

Fourteen READ-ONLY routes served by a **second NiceGUI process**,
`webgui/live_main.py` on `nicegui_live` (prod :8501, dev :9501), unauthenticated to
anyone. **They render the same page modules the private routes render** — each pin is
an optional keyword on the real `render()` — the precedent is
`sentiment_momentum.render(level=...)`, which `/sentiment/momentum` already took —
and every pin defaults to today's behaviour, so the app's own routes are unchanged
and a published screen cannot drift from the private one it mirrors. The table
below is not a second source: it is `webgui/live_screens.py:SCREENS`, which the route
registration, `tools/capture_live_shots.py` and the static grid on
`neuralstrike.co/live.html` all read. The invariants — the `import main` trap, the
four read-only layers, the Redis ACL — are in [CLAUDE.md](../CLAUDE.md); design + plan
in [`plans/2026-09-07-public-live-screens-design.md`](plans/2026-09-07-public-live-screens-design.md).

| Public route | Renders (private route) | Pinned |
|---|---|---|
| `/desk` | `desk.render()` (`/desk`) | — |
| `/opportunity` | `options.matrix.render()` (`/options/matrix`) | — |
| `/flow` | `options.flow.render()` (`/options/flow`) | — |
| `/macro` | `market.render()` (`/market`) | `macro_skin="B"` (Heat Lattice) — an `app_settings` pin, not a render kwarg, because the page reads it from settings |
| `/sentiment` | `sentiment.render()` (`/sentiment`) | — |
| `/bullbear` | `sentiment_bullbear.render()` (`/sentiment/bullbear`) | — |
| `/sectors` | `sentiment_sectors.render()` (`/sentiment/sectors`) | collapsed — already the page's own build state |
| `/rotation` | `sentiment_rotation.render()` (`/sentiment/rotation`) | — |
| `/rrg` | `sentiment_rrg.render()` (`/sentiment/rrg`) | — |
| `/momentum` | `sentiment_momentum.render(level="industry")` (`/sentiment/momentum`) | Industries |
| `/gamma` | `options.gamma.render(symbol="$SPX", view="GEX")` (`/options/gamma`) | `$SPX` · GEX |
| `/net-premium` | `options.gamma.render(view="Net Prem")` (`/options/gamma`) | group `indices`, symbols `SPY QQQ BIG10`, mode `dollars` (settings pins) |
| `/premium-divergence/spy` | `options.gamma.render(symbol="SPY", view="Flow")` (`/options/gamma`) | SPY · Flow |
| `/premium-divergence/qqq` | `options.gamma.render(symbol="QQQ", view="Flow")` (`/options/gamma`) | QQQ · Flow |

`BIG10` is a symbol inside the `indices` group in `config/symbols.toml`, not a group
of its own.

**A pinned gamma screen differs from the private page in three visible ways, all
deliberate.** It **draws no view picker** — `gamma.shows_view_picker(view)` gates the
build, so nothing can render empty because there is no control to click, and
`_PinnedView` stands in so the dozen downstream `view_toggle.value` readers are
untouched. It draws **no Refresh / Explain / Analyze / Briefings / History row, and the Symbol
dropdown becomes `_PinnedSymbol`** — a button that cannot work must not be drawn, and
`gamma.may_enqueue(symbol, view)` gates every enqueue site behind it as the total
proof. And its **three report watchers are unwired**: `_watch_explain` /
`_watch_analyze` / `_watch_history` open a new browser tab when their cache version
moves, and the version still moves because the OWNER can click Explain on the private
app — left wired, one private click would pop a tab in every anonymous visitor's
browser, pointed at a route this process does not serve.

The four gamma screens read **`cache:options:gamma_pub:<SYMBOL>`**, never
`cache:options:gamma` — that key is a sticky, symbol-agnostic single slot whose symbol
follows whatever the private app last looked at (see CLAUDE.md). `/net-premium` is the
exception and needs no per-symbol key: `cache:options:net_premium` is multi-symbol and
symbol-independent by construction.

**Every screen carries a slim brand header (2026-09-09).** The brand reached these
screens through the browser TAB TITLE alone, which is invisible on the YouTube wall
stream and on a kiosk — so a stranger opening one saw a dense trading board belonging
to nobody. `live_main._header(screen)` draws the app header's LEFT half and nothing
else: `shell.brand_lockup_html()` (mark + two-tone wordmark), a hairline, and
`screen.title`, over a `border-b` band about 40px tall. The mark is sized 32px here
rather than the app's 44px — `LIVE_HEADER_CSS`, this entrypoint's one `ui.add_css`,
and the documented escape hatch since `.brand-mark` lives inside a raw HTML string.
The screen name reuses `shell._CRUMB_LEAF`, the private breadcrumb's leaf style, so
the two cannot drift.

⚠ **The header links to NOTHING, and that is the design.** Settings, Terminate, Sign
out and the whole rail do not exist in this process; a link to a route this origin
does not serve reads as broken, and one pointing at the private host would advertise
it. ⚠ It also forced the **one non-page route** this process serves: `[brand].mark` is
a file under `/static`, and `live_main` mounts that directory (measured before the
mount, `:8500/static/img/neuralstrike-mark.svg` was 200 and `:8501` was 404). Mounting
a directory publishes every file in it — three alert WAVs and four brand images, no
config and no data — and `tests/test_live_main.py` pins both halves. `/voice` is not
mounted. A missing or misnamed mark still degrades to the **wordmark alone**, never a
broken-image icon.

**The brand builders moved to `shell.py`** with that header — `brand_mark_src`,
`brand_lockup_html` and `_STATIC_DIR`, re-exported by `main` so `wall.py` and the
tests are unchanged. It is what made `shell.py` stop being import-free; the reasoning
is in CLAUDE.md and the closed import list is pinned by `test_shell_seam.py`. ⚠
`IS_DEV` is a by-value export, so a test patching the DEV chip must patch it on
`shell`, not on `main`.

**The rest of the shell is not `_layout`.** The live process mounts no rail, no tab
strip, no breadcrumb, no market marquee and no page-help tooltips, and its content
wrapper is a neutral `ns-app w-full p-4 gap-3` column — deliberately NOT `theme.PAGE`, since every one of
the fourteen pages already supplies its own top-level wrap and background
(`CONSOLE_PAGE`, `RT_VOID_BG`, `macro-board`, `calc-v2 PAGE`), so wrapping again would
draw a second frame around each and a navy gradient behind the void-black ones. What
it DOES inject — because these follow the PAGE rather than the shell — is
`shell.TABLE_CSS`, `shell.SUBTAB_CSS`, `shell.PANEL_SCROLL_CSS`, the app surface and
boxed fields `_layout` also paints (`theme.SURFACE_CSS` / `APP_FIELD_CSS` under the
`ns-app` scope, plus `ui.colors(**theme.QUASAR_COLORS)`) and the `[typography]` /
`[brand]` font head. ⚠ `APP_FIELD_CSS` goes in BEFORE `SUBTAB_CSS`: it carries generic
`.q-tab` rules at the same specificity as `.compact-subtabs`', so injection order is
what decides, and the subtab row's own look must win. Without them `/opportunity` and `/flow` lose their sticky Deep
Slate table headers, `/net-premium`'s group picker draws as stock Quasar tabs, `/desk`'s
panels clip their rows and then scroll the whole document sideways, and every screen
renders in a different typeface from the private page it is supposed to mirror.

**Exposure is a recorded decision, not an oversight.** The screens are unredacted:
`/desk` renders merged paper and driver positions with rescue flags, `/opportunity`
ranks actionable signals, `/flow` carries live alerts. Anyone may read the book and
mirror the entries in real time. Chosen over redaction and over a 15-minute delay
because the book is **paper only** and full transparency is the argument the public
site already makes.

**Two things a PAGE must ask the shell, and the one it must not assume (2026-09-07,
from an adversarial review of the three findings below).** `webgui/shell.py` — the
seam both entrypoints provide — now carries the process's own identity, set once by
`live_main` before any page is imported and never by `main`:

* **`shell.may_enqueue()`** — may a control on this render put a command on a
  `cmd:` stream? False on the public origin. Every published page resolves it once
  into a local `_may_enqueue`, and the button is not built while the handler opens
  with `if not _may_enqueue: return`. `bus_client.request`'s `PermissionError` stays
  the backstop; both, not either — relying on the refusal alone leaves live buttons
  whose every anonymous click writes a traceback into journald, since `ui_guard.guard`
  re-raises anything that is not the deleted-slot error. It is gated on the ORIGIN
  rather than on `bus_client.is_read_only()` because the same fact answers the route
  question below, and a bus MODE says nothing about which routes exist.
* **`shell.route_for(route)` / `can_navigate` / `navigate_to`** — a page names the
  route the PRIVATE app serves (the address it has always known) and asks where that
  lives HERE. The public origin publishes most pages at a different path
  (`/options/matrix` → `/opportunity`) and some nowhere at all. The map is DERIVED
  from `live_screens.SCREENS`, which is why every `Screen` now carries a
  `private_route`; there is no second table. First screen wins, so the four
  `options.gamma` screens resolve `/options/gamma` to `/gamma` — the one place that
  table's ORDER is load-bearing. ⚠ An unpublished route resolves to **`None`**, never
  to the private path (a 404 here) and never to `app.neuralstrike.co`: the public site
  must not advertise the private app. The Desk's three position books are exactly that
  case, so those rows draw every number and lose the pointer, the hover wash and the
  handler.
* **What a page must not assume is that a settings pin reaches every caller.**
  `voice_enabled` is pinned False because a spoken alert is an `edge_tts` call to a
  Microsoft endpoint plus an mp3 on disk; the pin covered `desk.speak_phrases` and
  `desk._prewarm_clips` and missed `_unlock_voice`, which is reachable from a browser
  console in two messages (`emitEvent('desk_voice_blocked')` reveals the hidden button
  — `ui.on` subscribes on the client LAYOUT, which is visible, so NiceGUI's
  hidden-element event gate does not apply — then click it). Hidden is not absent.

In the private app all three resolve to today's behaviour exactly: `may_enqueue()` is
True, `route_for` is the identity, and voice defaults on.
