# CHANGELOG — WebGUI Trading with Schwab

The running log of dated session entries ("**Last updated** / **Prior —**") that used to sit at the top of [CLAUDE.md](../CLAUDE.md). Newest first — **append new entries at the top**. Durable architecture + conventions stay in CLAUDE.md.

---

**Last updated:** 2026-08-28 (**The Heat Lattice's option-skew line was invisible on green tiles.**
- **Measured live on the running board, not eyeballed.** In Skin B every tile is painted with
  its heat fill, which at full magnitude is `rgba(0,229,160,.36)` over the void — an effective
  `#015642`. Against that, the descriptor line's `MB_FAINT` `#3D4F6B` came out at **1.08:1**
  and the symbol's `MB_DIM` at **2.2:1**. Even the *coolest* lattice tile only reached 2.26:1,
  so the line was never really readable; green just made it total. Green is the worse of the
  two directions because green carries most of the luminance in the sRGB coefficients.
- **The fix is a Skin-B-only text ramp**, `[macro].lattice_sym` `#C6D6EA` +
  `[macro].lattice_desc` `#A9BFDA`, emitted by `build_macro_css` under
  `.macro-board.macro-b .mb-tile`. Skin A's tiles stay dark, so its ramp is untouched and
  verified so.
- ⚠ **The symbol had to move too, and that is not scope creep.** Lifting only the descriptor
  past 4.5:1 would have put the tertiary line *above* the ticker that names the tile. The
  test pins the reading order (price > symbol > descriptor) alongside the contrast floor.
- **Why this colour pair lives in the CSS block rather than `.classes()`:** `_set_skin` swaps
  the skin class on the WRAPPER and does not rebuild the 69 tiles, so a Tailwind class on the
  child cannot say "only when the lattice is on" without repainting every tile per toggle.
- **The descriptor also went 9px → 10px, which forced `min-h-[94px]` → `[96px]`.** Measured:
  the bump alone produced 95.5px for tiles WITH a descriptor against 94px for those without —
  exactly the two-height split the 2026-08-16 uniformity work removed. The floor has to clear
  the tallest tile.
- **Tests pin the property, never the hexes** — WCAG contrast recomputed from `heat_class`'s
  own alpha ramp, so a palette edit that re-breaks it fails in `test_market.py`. Confirmed
  discriminating by re-running it against the old ramp (fails at 1.04:1).
- **Verified in the live browser:** worst skew tile **1.08 → 4.75:1**, symbol 2.2 → 6.06:1,
  all 69 tiles uniform at 96px, Skin A still `rgb(61,79,107)`/`rgb(107,127,158)`, and the
  rules win over the Tailwind arbitrary class with **no `!important`**. webgui **2875 passed**.)

---

**Last updated:** 2026-08-25 (**The Trade detail speedometer became a score bar, and the expansions were reordered.**
- **The gauge is gone.** `svg.score_bar_svg` replaces the Highcharts angular gauge in the
  panel header — dark track, gradient fill running dark → the value's own `value_color`, a
  tick at the fill edge, the number beside it. The red/amber/green mapping is deliberately
  unchanged rather than a fixed green ramp, or a 20 would look as healthy as an 80.
- **`gauge_metric` now returns `value: None` when there is no score, not `0`.** The gauge had
  no way to draw absence, so 0 was the only option and the caption carried the meaning; the
  bar draws a bare track and an em dash. When a renderer gains the ability to show absence,
  the producer has to stop flattening it — the same lesson as `signal_band`.
- ⚠ **The panel no longer provides a Highcharts ESM anchor**, and that WAS a documented
  invariant. The old docstring said the gauge must live in `render()` so the ESM registers at
  first paint. Checked before removing: the gauge was the ONLY chart on all four pages that
  mount the panel, so none of them loads Highcharts at all now. Any of those pages gaining a
  chart created after first render must bring its own anchor. Pinned by an AST guard.
- **Expansion order is now Expected Move · Greeks · Implied volatility · Score factors** and
  is pinned by test, because it is a deliberate choice rather than an accident of growth.
- **Two prefix traps found by their own tests.** `<line` is a prefix of `<linearGradient`, so
  the marker-position assertion read the gradient's `x1="0"` and reported a tick at the
  origin. And the source guard for "no `ui.highchart` here" matched the module's own comment
  EXPLAINING the absence — now AST-based.
- **Verified in the dev browser:** `highcharts-container` count **0**, the gradient survives
  DOMPurify (stops `#463712` → `#daac37` on a 62), marker x == fill width == 118.2, label
  "62". webgui **2873 passed**.)

---

**Last updated:** 2026-08-25 (**Expected value in the Trade detail panel — and the version of it that was worth showing.**
- **The ask** was to display `EV = p*b - (1-p)` as a recommendation. Measured first, and the
  obvious implementation turned out to be harmful rather than merely useless. Design +
  plan: [`docs/plans/2026-08-25-ev-in-trade-detail-{design,plan}.md`](plans/2026-08-25-ev-in-trade-detail-design.md).
- **Finding 1 — the priced EV is ~0 in the median and junk in the tail.** Across the 75 live
  signals carrying usable fields, median priced EV was **+0.038 R**, as no-arbitrage requires.
  The top of the distribution was entirely bid-ask width: UAL **+2.832 R** at a *225%* relative
  spread, IBKR +0.751 R at 239%, AMGN +0.412 R at 395%. Ranking on it ranks the least
  trustworthy marks first.
- **Finding 2 — `p` and `b` must name the same event.** Directional signals (390 of 465, **84%**
  of the board) use `max_profit`/`max_loss` with no `credit` and no `short_delta`. Computing
  `b = max_profit/max_loss` there printed **+2137 R for a long put** — whose `max_profit`
  assumes the underlying reaches zero, while `pop_pct` is P(any profit). ⚠ The existing
  `unbounded` flag reads **False** for every one of the worst offenders, so it is not the guard.
- **Finding 3 — the family key nearly shipped broken.** `signals.db` stores `scanner_type`
  `'0DTE'`; a live signal carries `trade_type` `'0-DTE'` and a `scanner_type` of `None`. Keyed
  on the raw value the 0-DTE bucket would never have matched page-side — silently, for the
  family with the most recorded data. One normalizer (`shared.calibration.family_key` /
  `bucket_key`) is used by BOTH tiers, with a test on each side.
- **What shipped.** Two rows in the panel's ECONOMICS block. **Needs** — the win rate the
  trade's own price demands, `max_loss/(credit+max_loss)` — under `Probability`, so the margin
  needs no arithmetic. **Signals like this** — realized R per trade for the signal's family and
  score band, from the new `cache:options:calibration`. `expected_pnl_10` (the priced EV, in a
  collapsed expander since it was written) was **removed**; the engine still computes it where
  it belongs, as the width gate in `select_best_width`.
- **Both rows are omitted ENTIRELY when they cannot be honest** — not dashed, not flagged. The
  calibrated row is additionally withheld when its bucket's day-clustered t is inside ±2.
- **Tier 2.** `services/options_svc/calibration.py` + `handlers.refresh_calibration` +
  `scheduler.calibration_due`, on a new nightly `config/sessions.toml [slots.calibration]` at
  16:30 CT (after `[windows.collection] stop`, so the day's outcomes have settled). Reads
  `signals.db` only — no Schwab call, no Claude call. Payload is **984 bytes** on prod's 793
  closed signals and carries no per-trade rows and no timestamp, so an unchanged night
  `skip_unchanged`-skips the write.
- **The math moved to `shared/calibration.py`** so the CLI and the service share one copy
  rather than repeating the `clamp`-times-nine trap. Verified behaviour-preserving: the 39
  existing tests passed **unchanged** and the prod CLI output was **byte-identical**.
- ⚠ **Expect it to look inert at first.** On prod today only **3 of 7** buckets clear the t
  gate (`0DTE|55-60`, `0DTE|60-65`, `SWING|65-70`); `SWING|55-60` — 221 trades, the largest
  bucket — reads tDay **-0.89** and stays silent. That is the feature working.
- **Tests:** +35 shared, +22 options_svc, +19 webgui, tools split 39 -> 19 CLI + 35 shared with
  the SQL path now covered. Suites: webgui **2849**, options_svc **1244**, tools+shared **1127**.
  The loop's own `test_the_loop_test_leaves_no_branch_work_running` guard caught the new branch
  missing from `_BRANCH_HANDLERS` — exactly what it is for.)

---

**Last updated:** 2026-08-24 (**A missing composite published the most bearish band there is.**
- **The symptom.** With no composite to read, `cache:sentiment:composite`'s `derived`
  carried `{"size": "0.70x", "bias": "Short", "signal": "Strong Bear"}` — the most
  bearish word in either vocabulary, at the smallest position size, indistinguishable
  from a measured reading. Six different ways of having no data all produced that one
  answer: no live and no backfill, a snapshot with no `composite`, a `total_score` of
  `None`, of `NaN`, or unparseable.
- **The root cause is a boundary, not a formula.** `_safe_float` defaults to **0.0**, and
  `live_composite.signal_band` is a **TOTAL** function over the reals — `>=9 / >=7 / >=5
  / >=3 / else` — with no absence branch. So the manufactured 0.0 falls out of its last
  return, and so does a NaN, which fails every `>=` for a different reason. The absence
  was laundered into a reading one line before the band was computed.
- **Two of the six genuinely reach production.** `handlers._composite_gate` raises on a
  malformed composite and aborts the refresh, keeping the last good cache — which is
  correct, and stops three of them. It skips when there is no snapshot at all, which is
  `live=None` + an empty backfill: `compute.load_live` swallows **every** exception and
  `_load_snapshots_cached` degrades to `([], [])`, i.e. **the service starting before the
  proxy is warm**. NaN passes the gate too, since `float(nan)` does not raise.
- **Three renderers, and the third is the phone.** The Desk strip, the Market Regime
  Console, and — via `options_svc.market_snapshot` → `market_console.signal_rows` — the
  **market snapshot pushed to Telegram/Discord** on the thrice-daily slots. During an
  outage that push read "BIAS: Short / SIGNAL: Strong Bear" with nothing to mark it as
  an absence. No trading logic reads these words (`derived["size"]` has no consumer
  outside the GUI, and the bridge's `position_size_modifier` is written and never read),
  so the blast radius was display — on the surface that reaches you when you are not
  looking at the app.
- **Why nothing caught it, which is the part worth keeping.** All three renderers guard
  the absence *correctly* and none of their guards could fire:
  `webgui/tests/test_desk.py::test_signal_band_facts_print_a_dash_for_a_cold_cache_never_neutral`
  asserts the exact right invariant — "'Neutral' is a reading. A composite that has not
  published is not one" — and passes, because it feeds `{"bias": None, "signal": ""}`.
  **The producer never emitted that shape.** The guard sat on the consumer side of a
  boundary the producer could not cross. The same self-contradiction was visible on
  screen: for identical no-data input `sentiment_pill_text` correctly rendered nothing
  while BIAS and SIGNAL two tiles over shouted Short / Strong Bear in red.
- **Fixed at the CALL SITE**, per the standing rule that only the caller knows what a
  missing input implies — not in `signal_band`, which has two other callers inside
  `live_composite` and faithfully mirrors the source app's `_update_position_modifier`.
  `derive_composite_extras` now resolves the total through the module's existing
  `_as_finite` and publishes `size = bias = signal = None` when there is nothing to band.
- **`None` specifically, not `""`.** Two of the three renderers gate on
  `size is not None`, and an empty-string triple is a **truthy tuple** that would render
  blank rather than dashed. The `except` fallback's `("—", "", "")` was a third shape
  with the same defect — a non-None `size` opens the populated branch and prints an
  em-dash modifier beside two blank words — and became `None, None, None` in its own
  red-green cycle.
- **`total` is untouched.** The first draft of the fix also moved it from NaN to 0.0,
  which would have quietly changed `velocity`'s input; `raw_total` now feeds
  `_as_finite` and `_safe_float` separately so the band is the only thing that moved.
  `velocity`'s own missing-input policy stays its own.
- **Three tests, two of them red first:** the five absence shapes, the raising-band
  fallback, and — passing from the start, by design — a genuine 1.00 composite still
  banding Strong Bear, so the fix cannot decay into "publish None whenever the news is
  bad". Verified end to end by feeding the real producer's output through all three real
  renderers: em dash at the muted/flat tone in every one. Suites: sentiment_svc **331
  passed / 1 xfailed**, webgui **2830**, options_svc **1222**, failing set empty in all
  three.)

---

**Last updated:** 2026-08-24 (**BIAS and SIGNAL replace VIX on the Desk top strip.**
- **By request.** The strip's `$VIX` quote gives way to the Market Regime console's own
  two verdict tiles. Both words are ONE producer call's output —
  `live_composite.signal_band(total)` returns `(size, bias, signal)` and `sentiment_svc`
  writes all three onto `cache:sentiment:composite`'s `derived` — so the strip reads
  them and derives nothing. Live: `derived = {"size": "0.85x", "bias": "Cautious",
  "signal": "Bearish"}` at a composite total of 3.88.
- **Label AND footer descriptor are imported**, not restated: `desk._BAND_TILES` is
  built from `pages.sentiment.SIGNAL_TILE_DEFS` and the tone from that page's
  `_word_tone`, so a wording or palette change on `/sentiment` reaches the Desk instead
  of leaving two screens describing one number differently. The descriptors earn their
  line — "Cautious" beside "Bearish" reads as one word said twice unless the tiles say
  that one is **positioning** and the other is **strength**.
- **Each tile is coloured from its OWN word**, and that is the load-bearing bit. The two
  carry different vocabularies (positioning `Long`/`Neutral`/`Cautious`/`Short`;
  strength `Strong Bull`…`Strong Bear`), so one shared tone would eventually paint a
  colour contradicting the word standing beside it. Measured in the browser at the live
  reading: BIAS `rgb(224,183,78)` amber, SIGNAL `rgb(242,100,107)` red. A cold composite
  prints the em dash at the muted tone and **never** "Neutral" — that is the middle
  reading, and an absent one is not a reading.
- **The THREE verdict tiles share one type size and one width** — BIAS, SIGNAL and
  MARKET REGIME, by request. They are peers, so a reader should not have to work out
  which of them matters most from how big it is; `_STRIP_WORD` and `_STRIP_VERDICT_W`
  are one constant each, read by all three, because a size change that reaches two of
  them and misses the third is exactly what a shared vocabulary prevents. MARKET REGIME
  came DOWN (28px / 236px) and the two band tiles came UP (19px / 160px) to meet at
  **24px in a 180px tile**.
- **24px is the largest size that fits, and the ceiling was measured, not reasoned.**
  JetBrains Mono advances 0.6em, so the widest word either vocabulary can print —
  `"Strong Bear"`, 11 characters — measures **126px at 19, 159 at 24, 172 at 26, 185 at
  28** (browser-probed at all five sizes). At 24px it needs a 180px tile once `_TILE`'s
  10px padding is counted both sides; three of those, the 168px clock and five 16px gaps
  leave the two score cards **455px at the page's documented 1877px minimum**, clear of
  their 440px floor. **At 26px the tile is 192px and the cards fall to 437px — the strip
  would wrap at a width the page claims to support.** The clock keeps its own 28px
  `tabular-nums`: a countdown is a running figure, not a verdict, and unifying it would
  be unifying two different kinds of reading.
- **The page's documented 1877px minimum is unchanged.** Verified live at 1920 (six
  tiles, ONE row, all 92px, no clipped descendant, no sideways scroll — with
  `"Strong Bear"` forced into both band tiles, not merely with today's shorter words)
  and again at exactly 1877 (panels 839px, zero overflow, strip still one row). The 8
  elements that do overflow at 1877 are the Bull/Bear chip names, which carry
  `truncate` on purpose. Four guards pin the arithmetic itself — the shared constants,
  the longest-word fit against BOTH vocabularies, the card floor at 1877, and one footer
  size across the three.
- **`/desk/live` moved with it, and had to.** The streaming mirror imports the Desk's
  `VIEWS`, so dropping a view the mirror still read would have left it rendering a cold
  VIX tile forever with nothing failing — the same class of trap as a pushed snapshot
  being a separate renderer. Its band tiles **resolve** the hex from the class
  `desk.signal_band_facts` already stamped (`band_tone_hex` → `_hue`), rather than
  re-deciding which word is bullish: the difference between a mirror and a second
  opinion. Verified live at `/desk/live` — six tiles, one row, no `#vix` node left.
- **`cache:options:header` left the Desk's poll batch**, VIX having been its only reader
  there: one fewer `:ver` probe every 2 s for the life of a session, and one fewer
  repaint trigger for a strip that could not change because of it. `VIEWS` is 10.
- **Tests:** 9 new on the page + 4 on the mirror, each mutation-checked (8 mutations, 8
  caught) — a shared tone, a cold cache inventing "Neutral", the dead view creeping back
  into `VIEWS`, restated labels, the strip unwiring from the composite, and the mirror
  re-deciding its own tone. webgui **2825 passed**.
- **Docs:** `page_help`, `webgui-routes`, the User and Reference guides (rebuilt), a
  dated superseded note on the design doc that argued VIX could not move, and two stale
  CLAUDE.md facts fixed in passing — the Desk view count (9 → 10) and a `/desk/live`
  line still claiming `/desk` goes one-column when narrow, which stopped being true on
  2026-08-20.)

---

**Last updated:** 2026-08-23 (**Sonnet 5 everywhere, and a prompt audit measured with
`count_tokens` rather than estimated.**
- **The migration was one line; the audit was the work.** Only one production call site
  still ran Sonnet 4.6 — `options_svc.compute._NEWS_MODEL`, the web-search phase. It was
  pinned there because Sonnet 5's support for the `web_search_20260209` tool version was
  undocumented at the time; that pairing **is** documented now (the `_20260209` variants
  run on Opus 5/4.8/4.7/4.6, Sonnet 5 and Sonnet 4.6), so the pin is retired. Live-probed
  end to end: the search fires (`server_tool_use` blocks present) and the driver lines
  parse.
- **⚠ Migrating that call flipped a SILENT default.** `_research_news` passes no
  `thinking` argument. On Sonnet 4.6 that meant thinking-OFF; on Sonnet 5 it means
  ADAPTIVE — and thinking tokens come out of `max_tokens`. A live probe returned a
  `thinking` block and **1267 output tokens** against the old `_NEWS_MAX_TOKENS = 700`,
  i.e. the migration alone would have started truncating driver lines. Thinking is
  deliberately left adaptive there rather than disabled: with thinking off Sonnet 5 is
  markedly less tool-eager, and firing the search is that call's entire job — a search
  that never fires returns training memory as if it were today's news, the exact failure
  the `_NEWS_ABORT_ERROR_CODES` scan exists to catch. `output_config={"effort": "low"}`
  bounds the spend instead, and the cap moved 700 → 1600.
- **The biggest single win was dead instruction text on a forced-tool call.**
  `gamma_tool.build_summary_prompt_bundled` appended `_INTRADAY_SUMMARY_ASK` /
  `_PREMARKET_SUMMARY_ASK` — a numbered free-text structure ("1. BIG PICTURE … 4. WHAT
  IF") plus "Cap the whole reply at 350 words". Both production consumers
  (`gamma_analyze`, `eod_briefing`) call with `tool_choice` forcing `submit_analysis` /
  `submit_eod`, so the model never free-writes: the output contract is the tool schema
  and the caller's system prompt, and that whole block was unreachable text billed on
  every call. Each ASK is now one line. The role sentence that opened each one went too —
  the caller's system prompt owns the role, and a second differently-worded identity in
  the user turn only competes with it.
- **Say the real budget or pay for what you throw away.** `_research_news` truncates to
  `_NEWS_MAX_LINES` (6) but never told the model, so a probe produced ten lines and four
  were discarded unread. `_NEWS_SYSTEM` is now an f-string on that constant — the prompt
  and the code cannot drift. Measured on a second live probe: **1267 → 810 output tokens
  (−36%)**, exactly six lines, none discarded. Output bills at 5× input, so the 46 input
  tokens this cost are bought back many times over.
- **⚠ A stale factual claim in `decider._cached_system` inverted its own conclusion.** The
  docstring said Sonnet 5's minimum cacheable prefix is ~2048 tokens and that the
  tools+system prefix measures ~800, concluding the breakpoint was inert. Measured with
  `count_tokens`: the floor is **1024** (Sonnet 5 and Opus 4.8 alike) and the prefix is
  **1078**. The cache is live and has been. Consequence worth knowing before anyone
  "tidies" that mandate: it clears the floor by 54 tokens, so trimming the driver prompt
  silently switches the cache off. That is why the driver prompt is the one prompt this
  pass deliberately did not shorten.
- **The driver's committed default was Opus.** `settings._resolve_model()` returned
  `claude-opus-4-8`, and the only thing keeping the 30-minute autonomous checkpoints on
  Sonnet was `shared/driver_model.txt` — gitignored and untracked, so a fresh clone, a
  wiped `shared/` or a new machine put them on Opus silently. The committed default is
  now `claude-sonnet-5`; the env var and file overrides still win, and the no-override
  path is pinned by a test that says why.
- **Deduplication, not shortening.** "Frame it as what the reader should DO, not what
  dealers are doing" appeared five times across `_ANALYZE_SYSTEM`, the `submit_analysis`
  tool description and three field descriptions. It now appears where it belongs — once
  in the system prompt, once on `narrative` where the shape genuinely varies. `_EOD_SYSTEM`
  stated its mandatory-fields rule twice and shouted both. Kept once, at normal volume,
  **with its reason attached**: that demand is not emphasis boilerplate, it mitigates a
  failure this briefing has actually shown (a `max_tokens` stop truncates the tool input
  and drops trailing fields, which reads exactly like the model choosing to omit them —
  the schema's `required` array does not prevent it).
- **What was deliberately left alone.** `market_svc._SUMMARY_SYSTEM` is clean — terse, and
  its numeric cap is genuinely enforced downstream (`text[:_SUMMARY_MAX_CHARS]`), so it is
  a contract, not a verbosity clamp. `_NEWS_SYSTEM`'s prohibition list stays because
  `_is_meta_line` / `_strip_markdown_emphasis` exist precisely because the model emitted
  those anyway. The `submit_eod` tool description carries contract only. An audit that
  finds nothing should change nothing.
- **Measured, not estimated** (`messages.count_tokens` against `claude-sonnet-5`, real
  builders, representative four-view/three-symbol data): `gamma_analyze` 5879 → 5455
  (−7.2%), `eod_briefing` 5935 → 5439 (−8.4%), `_research_news` 296 → 342 input but
  1267 → 810 output, `market_svc` and `driver_svc` unchanged. App-wide **33,656 → 32,072
  input tokens/day (−4.7%)** plus **−1,828 output tokens/day (−36% on that phase)** at the
  scheduled cadence — about **$0.03/day, ~$8/yr** at $3/$15 per MTok. Small in dollars
  because the whole Claude bill is small; the behavioral fixes (a truncating news phase, a
  cache believed dead, an Opus default one deleted file away) are worth more than the
  tokens.
- ⚠ **Sonnet 5's tokenizer emits ~30% more tokens for the same text**, so every
  token-denominated baseline in this repo shifts. Re-run `count_tokens` against
  `claude-sonnet-5` rather than reusing a figure measured on 4.6.
- **Suites:** options-scanner 1186 passed / 2 skipped, options_svc 1222, driver_svc +
  market_svc 316, shared/tests 152 — all green.)

---

**Last updated:** 2026-08-23 (**A streaming HTML mirror of the Desk — `/desk/live`
+ `/desk/stream`.**
- **Why a second screen at all.** `/desk` is a NiceGUI page: a websocket, a Vue runtime
  and a live client connection per tab. That is right for the screen you click through
  and wrong for the second monitor, the wall display or the phone — all of which want a
  page that survives a laptop sleeping with nothing to reconnect but an HTTP request.
  `webgui/desk_stream.py` serves the same screen as ONE static document plus a
  `text/event-stream`.
- **The rule that shaped it: the mirror COMPOSES, it never restates.** Every number
  comes out of a builder `/desk` itself calls — `dealer_rows`, `opportunity_rows`,
  `flow_rows`, `position_rows`/`positions_summary`/`summary_line`, `freshness_facts`,
  `countdown_facts`, `bullbear_chips`, every `fmt_*`. `snapshot()` is PURE and returns
  display-ready **strings**, so the client JS holds no formatting logic to drift with and
  the whole mirror is unit-tested without a browser or Redis. A mirror that re-derived so
  much as a rounding rule would be a second screen quietly disagreeing with the first —
  the `/sentiment/sectors`-vs-`/sentiment/rotation` bug, reproduced on purpose.
- **Two cadences, not one.** `clock` every second (the countdown moves every second, and
  it doubles as the SSE keep-alive — an idle event stream gets dropped by proxies, and a
  frozen countdown looks exactly like frozen data); `desk` only when a cache version
  actually moves, probed on the Desk's own 2 s `read_versions` batch. Pushing the whole
  screen once a second would be ~50x the bytes to animate one tile. The countdown is
  computed SERVER-side deliberately: every session bound comes from
  `shared.market_calendar`, and a JS countdown would need its own NYSE calendar.
- **The one genuinely new thing, and its guard.** The page modules express a data-driven
  colour as a fixed Tailwind class, which this document cannot render (it ships no
  Tailwind). `tw_hex()` resolves those finite maps — `flow._TONE`, `bullbear._CLASSES`,
  `matrix._SIGNAL_CLASS`, `header._REGIME_BG` — against a name→hex table, and returns
  **None** for a colour it does not know rather than guessing. That is what lets
  `test_every_flow_tone_the_flow_page_can_stamp_resolves_to_a_real_hex` fail when a new
  alert type appears, instead of the mirror silently painting it in a fallback hue. ⚠ Key
  coverage alone would have been VACUOUS there — `FLOW_TONE_HEX` is built *from*
  `flow._TONE` — so the assertion is on the hex being non-None.
- **Wired into the rail as "Live Mirror"**, pinned directly under Desk in the SAME
  caption-less landing block — it is the same screen for a different display, not a
  destination of its own, so filing it under MARKETS would have presented it as a
  distinct market read. It is the **one rail route that is not a shell page**, hence
  the only one that opens in a **new tab** (`EXTERNAL_RAIL_ROUTES`): it renders no
  `_layout`, so a same-tab navigation would strand the reader on a page with no
  drawer, and the row never claims the active wash. Drawer 14 -> 15 items, with the
  hover guide (`page_help`), a User Guide section and a Reference Guide section
  added in the same commit — the repo's own tests enforce all three, which is
  exactly the guard that keeps the manuals from rotting.
- **Verified live** (worktree, throwaway harness on :9700, read-only against prod's
  cache): 4 dealer rows, 6 board rows, 9 flow alerts, 8 of 39 positions with the
  `SHOWING 8` clause present, 11 Bull/Bear chips, the clock ticking off the stream, the
  stale-feed path withholding walls with "Walls withheld — GEX feed stale · 366m ago",
  the structure map's four marks placed, no overflow at 800px or 1920px. **+33 tests**
  (webgui 2320 -> 2353, all green).
- **The panel grid is ALWAYS 2x2** — a deliberate DIVERGENCE from `/desk`, which goes
  one-column below ~2104px. This screen gets pinned to a display and read from across a
  room, so a layout that reflowed on width would move every panel somewhere the eye does
  not expect. `minmax(0, 1fr)` is the load-bearing half (a grid item's automatic minimum
  is its content, so a bare `1fr` lets the tables push the PAGE wider instead of
  shrinking), `table-layout: fixed` makes the truncating cells actually ellipsize, and
  each panel body carries `overflow-x: auto` as the floor — a column too narrow even for
  the truncated table scrolls its own TABLE, never the page.

**Last updated:** 2026-08-22 (**Trade Analyzer long/short — Phases 0 through 6.
The swing model's refit is the headline, and it is not good news: Phase 0 found
the artifact 55 days stale and 44% of the measured edge gone on refresh, and
Phase 4 found that what remains is a beta bet.**

**Phase 1** — three of four tasks; the fourth turned into a sourcing decision.
- **The Investor verdict was silently running at HALF WEIGHT for every symbol
  ever analyzed.** `valuation` is the mean of {P/E vs sector median, PEG}, but
  `analyze()` passed `sector_pe_median=None` unconditionally, so
  `score_pe_vs_sector` returned its missing-input 0 and the mean HALVED the
  surviving PEG score — an excellent PEG scored +20 where it should score +40.
  Fixed at both ends: the mean now averages only sub-scores whose **inputs** are
  present (the availability test cannot be on the outputs, because `score_peg`
  legitimately returns 0 for a PEG between 1 and 2), and a new
  `compute.sector_pe_median()` computes a real median from the peers
  `_SYMBOL_SECTOR` already names, memoized per sector per day. Measured live:
  13 valid Technology P/Es, median **32.88**. MSFT's valuation now scores **35**
  where the old halving gave 20; AAPL's is a genuine −5 (it trades above its
  sector) rather than a structural 0.
- **Schwab serves short interest as a 0.0 SENTINEL, not as data.** Both
  `shortIntToFloat` and `shortIntDayToCover` are present in every
  `/instruments` payload and populated for NO symbol — measured live, 0.0 for
  AAPL, TSLA, **GME** and **CVNA** alike, while `peRatio`/`returnOnEquity` in
  the same response were correct. A listed optionable equity with literally zero
  short interest does not exist. Parsing it through would have disabled the
  planned short-side squeeze gate for every symbol forever with nothing on
  screen to say why — the documented "a 0.00% cell is not proof of a flat tape"
  trap. It now maps to `None`, so absence reads as absence.
- **⛔ No finviz (decision).** That finding promoted finviz from a convenience
  for earnings dates into the **sole supplier of both the earnings gate and the
  entire short side** — load-bearing scraping infrastructure. Declined; official
  and licensed sources are under evaluation instead (FINRA, exchange
  publications, vendor APIs, and the broker APIs Public.com / TradeStation /
  moomoo). **The short side does not ship until that lands**: the squeeze gate
  has no other supplier, and shorting crowded names unguarded is the exact tail
  it exists to avoid. The earnings gate consequently stays dead for now — the
  one Phase 1 goal that did not survive contact with the data.
- **Two forward-accruing stores started**, because neither can be backfilled and
  both pay in calendar time rather than effort. `rec_journal` records what the
  MODEL SAID (composite, band, percentile, both verdicts, gates, artifact
  version), keyed `(symbol, reading_date)` and upserted so a symbol analyzed
  five times in an afternoon casts ONE vote in the IC rather than five — those
  being exactly the names you were unsure about; its `fwd_5d/10d/20d` and
  `labeled_at` columns exist from the first row so Phase 6's labeler is an
  UPDATE, not a migration. `fundamentals_history` records the point-in-time
  **inputs, not the score** — a score is recomputable from inputs under new
  weights, inputs are not recoverable from a score — plus `sector_pe_median`,
  since valuation is relative and the peer median moves too;
  `margin_expanding` stays three-valued, because None means "the pair needed to
  decide was absent" and collapsing it to 0 would invent a bearish reading out
  of missing data.
- **The pytest isolation guard is real and verified.** Both writers no-op under
  `PYTEST_CURRENT_TEST` unless handed an explicit path: this repo has a
  documented incident where a suite wrote into live data (the bus is fakeredis;
  SQLite is not), and `analyze()` is exercised by many tests that know nothing
  about these stores. A full 105-test run creates no `services/trade_svc/data/`
  at all, while a real analyze writes both — verified end-to-end by enqueuing
  `cmd:trade` against the running dev service.
- Paths went into `repo_paths` per the no-hardcoded-paths rule
  (`TRADE_SVC_DATA`, `REC_JOURNAL_DB`, `FUNDAMENTALS_HISTORY_DB`), with
  `IV_HISTORY_DB` re-derived from the new directory constant.
- ⚠ Still open in Phase 1: the nightly UNIVERSE sweep. Both stores currently
  accrue only for symbols actually analyzed, and per the promotion freeze the
  sweep must be a standalone scheduled script rather than a service scheduler
  job — dev runs `schedulers: False`, so a service job would sit inert.
- Suites: trade_svc **105**, trade-analyzer **259**.

**Phases 2 and 3** — the short side becomes real, and the verdict becomes a plan.
- **The short side had no gates.** Below-200-EMA and sector-downtrend cap only
  BUY, so nothing stopped the model recommending a short into a healthy uptrend.
  Three short-only gates now: above a **rising** 200-EMA (the slope is
  load-bearing — price bouncing back above a still-FALLING 200-EMA is the
  textbook short entry, which a bare "above the 200" test would have gated
  away), sector in confirmed uptrend, and squeeze risk. They live in their own
  `short_gates` list, which came out of a test failure that was right: a
  short-only constraint in the shared list prints "cannot be SELL" on every
  strong BUY.
- **Direction clearance, and a bug only live data found.** The first version let
  a committed downward regime clear directional shorts outright; run against the
  real tape it returned SPY above a *rising* 200-DMA → longs cleared AND
  Softening → shorts cleared, a contradiction on its face. The 200-DMA is a
  multi-week structure while the committed direction comes from a 5-minute EMA
  slope — using the latter to authorise a **twenty-day** short is the same
  horizon mismatch the audit criticised in the legacy engine. The structural
  read now outranks the fast one. Everything fails conservative: unknown trend,
  missing regime and stale regime all land shorts on relative-only, and
  `spy_trend` returns **None** rather than False on thin history, because False
  reads as "below the 200-DMA" — one of the conditions that CLEARS a short.
- **Dealer positioning and sector peers reach the page**, and the universe
  snapshot keeps symbol identity (it computed every symbol's factors daily and
  threw the names away). The flat basis the scorer consumes is DERIVED, so the
  scoring path provably did not move. A legacy test caught that an empty
  snapshot must stay **falsy**, or a truthy `{"by_symbol": {}}` caches a
  snapshot of nothing for the day.
- **Earnings dates via Alpha Vantage — and the fail-open hole it exposed.**
  Measured with a real key: 1,814 symbols, coverage collapsing with distance
  (1,032 rows in October, 11 in March), **11 of 20 mega-caps missing**, and
  genuinely patchy rather than announced-only — AAPL and GOOGL appear at 67–68
  days while MSFT, AMZN and META, the same cycle, are absent. Both mega-caps
  inside the gate's own window WERE covered. The danger was that
  `days_to_earnings is None` meant both "nothing scheduled" and "not carried",
  so a hold could walk into an unlisted report wearing the appearance of
  protection. `coverage()` now separates them and the page says the date is
  UNKNOWN.
- **Two conventions unified** (Phase 3.1): one 25Δ skew sign (`put − call`,
  with the prose AND the colour flipped to match) and one gamma flip. The deep
  dive's cumulative-crossing flip sat **five strikes** from the per-strike one
  the collector stores; a cross-tier test now runs both on one grid.
- **The Trade Plan block** turns a verdict into something falsifiable:
  structure and tenor from a pure lookup (clearance outranks IV; walls set the
  short strike; unknown IV rank is MID, never cheap), entry zone, an ATR-or-wall
  stop that prefers whichever is TIGHTER and returns None rather than inventing
  a level, the calibrated target — and a **time stop at the model's own 20
  trading days**, resolved to a real date. Past that the read is unmodelled and
  nothing in the app had ever said so.
- Suites: trade-analyzer **288**, trade_svc **248**, webgui **2572**,
  options-scanner **1186**, schwab-proxy **104**.

**The Signal Desk — the Trade Analyzer rebuilt to a supplied design.** Four
screens behind one persistent command bar: Overview, Evidence, Rank board, Trade
plan.
- **Tailwind-first held.** The source design is entirely inline styles, which
  this repo forbids; every value is a token in `webgui/pages/terminal_theme.py`
  and the six new files join `test_no_inline_style.py`. Percentages (bar
  geometry, the dealer ladder, the decile marker) use runtime arbitrary classes
  — the documented continuous-value exception — while every COLOUR comes from a
  fixed finite set. Not config-driven, deliberately: a page-scoped palette whose
  numbers are chosen against each other, the same category as `sector_heat`.
- **The design's own tab bar was dropped**, because the app already renders one
  from the nav; the four screens are nav children instead, and the command bar
  keeps symbol, price, bias and the model stamp. The symbol is a DRAFT until
  committed — the box outlines indigo while your typing differs from what is on
  screen, and blurring an empty field reverts, because an empty symbol is not a
  request.
- **Mono is reserved for numerics**, so any monospaced text on screen IS a
  number — which is what makes a nine-column rank table scannable.
- **Absent stays absent.** The dealer ladder is withheld ENTIRELY when
  uncollected or stale; an investor factor with no data reads `n/a` with no bar;
  an unmatured history row reads `pending`. A dense mono grid reads as measured
  whether or not it was, which makes this app's documented failure mode worse,
  not better.
- **Five real defects surfaced by building it**, three of them only by running
  it: the rewrite would have DROPPED the Deep Dive and AI Query buttons (now in
  the command bar, reachable from all four screens); `plan_rows`/`dealer_rows`
  return DICTS and were unpacked as tuples (the plan screen 500-ed — pinned now
  at the boundary, since the webgui tests never execute a render body and ruff's
  F82 sees no undefined name); `analyze` fetched the quote's `change_pct` and
  stored neither, so the command bar showed a permanent em dash over data
  already in hand; `−0.00` rendered as a small negative; and the options
  matrix's `"na"` SENTINEL leaked into the dealer column as a literal value —
  normalised at the service boundary, with a test that a real 0 still survives.
- **The rank board gained real columns**, joined from one cache read for the
  whole board: dealer regime and IV from the options matrix, plus a genuinely
  side-specific metric — the calibration band for longs, FINRA's days-to-cover
  for shorts. `symbol_history` is new on the payload for the Evidence screen:
  the last five reads of one name, deliberately rows and not a statistic.
- Verified live across all four screens: tokens resolve exactly (`#080d17`
  ground with its radial lift, `#0e1626→#0b1220` panels, Manrope + JetBrains
  Mono), and the decile marker sits at 89.9% of the rail for a 90th-percentile
  read.
- Suites: webgui **2676**, trade_svc **385**; ruff and pyright clean.

**Phase 6 — the feedback loop.** The journal has recorded what the model SAID
since Phase 1; this records what happened next. Phase 4 decided the shape of all
of it.
- **The labeler is beta-aware, because otherwise it would lie.** The model's
  measured edge IS beta, so a monitor scoring itself on the raw forward excess
  would report health straight through any rising market. Each horizon stores
  three numbers — raw excess, beta-adjusted, and the market's own move — added
  to `rec_journal` by MIGRATION, since a store that cannot be backfilled must
  never be recreated to gain a column. Horizons are TRADING bars, matching the
  fit; unknown stays NULL, because an unmatured horizon is not a flat outcome
  and an unmeasurable beta does not become 1.0.
- **The monitor refuses two temptations.** It will not print an IC from a dozen
  readings — that is no measurement, not a thin edge — and it will not compare
  its POOLED correlation to the artifact's per-DATE cross-sectional OOS IC.
  Those are different statistics, and printing them adjacent would manufacture a
  decay finding out of a units mismatch. `decay` populates only from the
  comparable statistic, which with sparse live readings usually means not at all.
- **The refit runs monthly and reports what moved** — and was validated against
  the two REAL reports: it reconstructs the Phase 0 finding unaided, *"-44%, the
  measured edge fell by more than a quarter"*, which is precisely the change
  that went unremarked for 55 days.
- **The model paper book** follows the board's pools by the Trade Plan's rules,
  honours the market filter (a relative-only short is held as a PAIR against
  SPY) and declines gated names. Verified live: **13 positions, 6 long
  directional and 7 short relative**. ⚠ It trades the UNDERLYING, a stated
  deviation — a spread's theta and vega would make a book that lost money on
  correct calls indistinguishable from one whose calls were wrong.
- **Four bugs, three of them findable only by running it.** `score_symbol` never
  returned `band`, so every journalled row carried NULL in a column
  `journal_reading` had always written. `rec_journal.init_db`'s default argument
  binds at DEFINITION, so monkeypatching the module attribute did nothing and
  the first labeler tests opened the **real journal** — contained by the
  worktree, which was luck. The book's tick built candidates from an empty price
  map and fetched quotes afterwards, so it opened nothing while every unit test
  passed (they all inject prices). And the card's new lines referenced a name
  that does not exist in that scope, 500-ing the whole Trade page — **ruff's F82
  catches that and is already in this repo's select list**, so the guard
  existed; it just is not part of the test run.
- **Still open:** Investor validation is BLOCKED (`fundamentals_history.db`
  started 2026-08-22, needs ~2 quarters), and a third book in the EOD report is
  deferred until the book has closed trades to report.
- Suites: trade_svc **418** (with contracts), webgui **2619**, tools **837**;
  ruff and pyright clean.

**Phase 5 — the rank board.** The Analyze card answers "what about THIS name?";
the board answers "of everything the model can see, what is best and worst right
now?" — the shortlist the single-symbol page was always missing.
- **One code path, and threading it found a latent bug.** Every row is scored by
  the same `score_symbol` the card calls, with the same basis AND the same
  regime key. `_peer_block` had been scoring peers with NO regime while the
  headline symbol was scored with one, then comparing their percentiles —
  invisible only because the artifact carries a single regime today.
- **Deciles come from TODAY's cross-section**, not the artifact's calibration
  bands. Pinned by a test that hands the artifact ONE band, making every name
  historically indistinguishable, and still requires the board to rank them.
- **Gated rows are marked, never dropped**, and `gates_evaluated` is published
  beside them — the board checks a SUBSET of the card's gates, so an unmarked
  row must not read as "cleared everything". Its squeeze gate calls
  `short_interest.squeeze_flag` with the float leg absent rather than
  re-implementing the threshold, so board and card cannot drift.
- ⚠ **The first live build returned zero rows, and that was a real bug.** The
  cached universe snapshot was in the FLAT `{factor: [values]}` shape
  `get_universe_snapshot` deliberately tolerates from older code — scoring works
  against it, ranking cannot, because it has values but no symbol NAMES. On
  screen that is indistinguishable from "the market offered nothing today". Now:
  a `status` naming which kind of empty it is, a self-heal that rebuilds a legacy
  snapshot once instead of waiting out the day, and a page that renders the
  reason — or NOTHING for an unrecognised status, rather than guessing. A fourth
  fix fell out of the third: `status` was in the builder and the page but not the
  contract, so the projection dropped it silently between service and page.
- **Verified live in dev** (78 names): 25 rows carry visible gates (NVDA
  earnings in 4 days; ORCL earnings in 17 days *and* below its 200-EMA), and the
  short pool renders as *"Express these RELATIVE… SPY above a rising 200-DMA"*.
- **The live run demonstrates Phase 4 better than any table.** Long pool: MU,
  INTC, AMD, AMAT, QCOM, CAT, AVGO, TXN. Short pool: TMO, DIS, BAC, PFE, V, ABT,
  MA. The buy list is high-beta semiconductors and the sell list is defensives —
  which is why the amber exposure line sits directly above the table.
- Trade Analyzer became a nav GROUP (Analyze · Rank Board). The manuals guards
  caught it immediately: both manuals gained a Rank Board section and the
  renamed Analyze heading, and two stale nav tables were corrected.
- Suites: trade_svc **346** (with contracts), webgui **2616**.

**Phase 4 — the model refit, which turned into finding out what the model is.**
- **The swing composite is a beta bet, not a cross-sectional edge.** Splitting
  the panel on the market's own forward 20-day return: composite IC **+0.1598
  when SPY rises, −0.1142 when it falls**. Nine of fourteen factors flip sign
  with the market — `downside_beta` −0.1923/+0.1702, `low_vol` −0.1507/+0.1195,
  `semivol` −0.1465/+0.0991 — and the down-market weight set is nearly the
  negation of the up-market one. Over a window that was roughly 2:1 up, that
  nets to exactly the small positive OOS IC every study measured.
- **The cause is the LABEL.** `r_symbol − r_SPY` is a raw excess return, so a
  high-beta stock earns positive excess whenever the market rises —
  mechanically, no skill. Fit over a mostly-rising five years, any model on this
  label MUST discover that volatile names outperform. `research/labels.py` adds
  the textbook alternative, `r − beta·r_market`, with beta on a **trailing**
  window (a full-sample beta would leak the future into the label itself).
- ⚠ **The regime split could not have caught it.** `highvol` is a VOLATILITY
  regime, so a violent rally and a violent selloff both land in it. And
  splitting on a FORWARD market return is look-ahead — labelled a diagnostic
  throughout, since the question is not what to buy but what the model does when
  the market falls.
- **Four of six tasks measured NOT to adopt.** Noise floor: no floor differs
  from 0.005 (all |t| < 1.4). Universe 78 → 173: t = **+0.82**, and it costs
  live latency because the artifact's `fit_universe` IS the cross-section
  `trade_svc` snapshots daily. Regime-conditioned weights: **worse** (+0.0128 vs
  +0.0206). **C13 is refuted outright** — `low_vol` carries the same sign in all
  three regimes (trend −0.0972, chop −0.1254, highvol −0.0721), stronger in each
  than pooled.
- **The two that WON are the ones not to ship.** Orthogonalized residual IC
  (+0.0834, t = **+3.01**) and the four-factor short slate (+0.0698, t =
  **+2.64**) are both pre-specified fixes for documented problems, and both win
  by concentrating weight on the volatility cluster. The orthogonalized
  weighting's down-market IC (−0.1282) is *worse* than the scheme it would
  replace. Nine comparisons ran this phase; a Bonferroni-style correction at 13
  folds would want |t| > ~3.4 anyway.
- **Calibration moved OUT OF SAMPLE** — the one unambiguous win. The artifact
  calibrated its bands on the rows its weights were fitted on, so the
  "calibrated mean" the page prints as an expectation was in-sample. Measured:
  top-band hit rate **49.86% OOS against 52.68% in-sample**. The in-sample set
  is retained as `calibration_insample` so the flattery is visible. The bottom
  band's edge is real (**−0.93%** vs SPY over 20 days against **+0.85%** at the
  top) — but ⚠ **every band's hit rate is below 50%**, top included, because the
  label is excess return vs a cap-weighted index.
- **The exposure is now on the card.** The factor registry gained a `family`;
  `score_symbol` derives `risk_share` and the evidence expander states it, with
  a reversal caveat above 30%. The live artifact reads **47.6%**.
- **The harness is the reusable part.** `research/panel_cache.py` fetches once
  and keys on anything that changes the panel's content, **including the factor
  registry**; `paired_delta` tests two variants on the SAME folds. Phase 0 moved
  OOS IC 44% on a refetch alone, so without this every comparison above would
  have been swamped by fetch noise.
- **Regime machinery ships with the keys EMPTY.** 5 years gives 653/182/149
  regime-days against a 441-day floor for one walk-forward fold, so only `trend`
  qualifies — and at 66% of the sample it would be the pooled fit under another
  name. `regime.py`, `artifact.build_regimes`, the scorer selector and the card
  line are all in place for a fit that can fill them honestly.
- **Root-cause test — with beta priced out, nothing is left.** Rebuilding the
  same panel's labels as `r − beta·r_market`: the up/down gap collapses **0.2739
  → 0.0104 (−96%)**, confirming the label was the cause — and the edge goes with
  it. OOS IC **+0.0674 raw → −0.0101 beta-adjusted** (orthogonalized: +0.0853 →
  −0.0009); paired **−0.0774, t = −2.15**.
- ⚠ **The beta-neutral calibration is worse than flat.** Smoothed, all five
  bands read −0.0006 at 48.14%, which looks like "no edge". UNSMOOTHED they are
  non-monotone with a **negative** top-minus-bottom (−0.00147) and hit rates
  that DESCEND across the ranking (48.94% → 47.27%) — the isotonic smoother
  pooled four of five. The control makes it readable: on the raw label the same
  code left all five untouched and cleanly ordered, so the flattening is the
  data, not the transform. **`calibrate` now records `pooled` per band**,
  because after smoothing "flat" and "no ordering at all" are
  indistinguishable and only the second means the model cannot rank.
- Momentum was partly beta too (`mom_6_1` +0.0172 → **+0.0014**, `mom_12_1`
  +0.0156 → **+0.0040**, both dropped). The only factor that IMPROVES
  beta-neutral is `str_5d` short-term reversal, +0.0092 → **+0.0217**.
- **The gate FAILED on the question it stood in for**, so the Phase-0 artifact
  stays primary. Per the plan, a documented negative result is a completed
  phase. What remains is a **product** decision — keep the model with the
  disclosure now on the card, reframe it as the volatility ranking it is, or
  demote it to the legacy heuristic — and it is deliberately not taken here.
- Suites: trade-analyzer **392**, trade_svc **261**, webgui **2581**,
  options_svc **1222**.

**Phase 0** follows.
- **Program docs:** [design](plans/2026-08-22-trade-analyzer-longshort-design.md)
  + [plan](plans/2026-08-22-trade-analyzer-longshort-plan.md) — an audit of the
  Position (1–8 wk) and Investor (months+) verdicts turned into a six-phase
  build for two-sided (long **and** short) research and recommendations. Phase 0
  is bench-clearing only; Phases 1–6 are designed, not built.
- **The artifact was 55 days stale, and refreshing it cost 44% of the measured
  edge.** `fit_swing_model.py` re-run **unchanged** (same methodology, same
  78-name universe, fresh 5-yr window through today): **composite OOS IC +0.0367
  → +0.0206**, 6 of 13 folds negative (was 5). The decay is concentrated in
  exactly the factors with the theoretical grounding — `mom_12_1` mean IC
  +0.0407 → **+0.0183**, `mom_6_1` +0.0332 → +0.0214, `trend_quality` +0.0228 →
  +0.0104 — while `low_vol`, the audit's least-defensible factor, **grew** its
  share to −0.391 (39% of absolute weight, still on the inverted sign). The
  calibration bands held (top band 52.29% → 52.68% beat-SPY, spread ~2.4%), so
  the RANK still separates outcomes; the composite's ability to order the
  cross-section is what fell. The old artifact + report are archived under
  `trade-analyzer/data/archive/2026-06-28/`.
- **"Kept 9/10 factors" (was 6/10) is a symptom, not an improvement.**
  `signed_ic_weights` admits anything with `|mean_ic| > 0.005`; as the strong
  factors decayed, noise crossed the floor. **`rs_spy` now carries a NEGATIVE
  weight (−0.059)** — the model mildly rewards a stock for LAGGING SPY, which is
  backwards for a momentum model and visible to the user in the evidence
  expander. Verified live on AAPL: `z −0.300 × w −0.059 = +0.018`, i.e. AAPL
  scored *up* for underperforming. This is C12 (univariate IC weighting over a
  correlated momentum cluster) meeting a too-permissive noise floor, and it has
  moved from a deferred theoretical finding to something in production. Phase 4
  now leads with a **measured** floor study (re-fit at several `min_abs_ic`
  values, adopt only if it wins OOS) *before* any new factors, so a floor change
  and a factor change are never confounded in one OOS number.
- **This does not argue against the refit.** The new artifact is fit through
  today and is the more honest estimate; the old one was scoring a changed market
  with June weights. It does argue loudly for the regime work the artifact's
  `regimes` keys were built for and which has still never been fit.
- **⚠ The validated swing model has NEVER run in prod, and could not have.**
  Found while verifying the refit: `D:\WebGUI Trading Prod\trade-analyzer\data\`
  **does not exist**. `SWING_MODEL` resolves under each checkout's own root, the
  artifact is gitignored, and `promote.bat` moves code via `git pull --ff-only`
  — so no promotion has ever carried an artifact across, and none ever will.
  `load_artifact()` → `None` → `swing_block` → `None` → `trade.py` renders
  `_legacy_verdict_body`. Confirmed from both ends: the file is absent, **and**
  prod's own `cache:trade:analysis` (db 0, AVAV) carries `swing_model: null`
  beside a legacy `position_verdict` HOLD 31. **Prod's Position card has only
  ever shown the legacy 5-minute-bar heuristic** — the engine the validated
  model exists to replace. Deploying an artifact to prod is a manual copy or a
  prod-side fit run; it is a deliberate decision (it changes what the card
  displays), so Phase 0 stops at dev and flags it. **The general lesson: for
  anything under a gitignored `data/` directory, "shipped to dev" and "live in
  prod" are different states, and `promote.bat` does not bridge them.**
- **A correction to this session's own earlier reporting:** the regime figures
  quoted while auditing (trend score 41.21, A/D 0.51:1) came from **dev's**
  Redis (db 1), whose schedulers are suppressed, so they were a stale 2026-08-20
  snapshot. Prod's live read today is **trend score 58.61** (still labelled
  Neutral) with the regime at Trending · Softening. The qualitative
  "softening tape" characterisation holds; the number did not. A process
  resolving `ENV_NAME` from a worktree or the dev root reads **db 1** — check
  which database you are on before quoting a live figure.
- **The wall-side bug is fixed** (`options_svc/compute._gex_from_snapshot`). It
  read `gamma_walls()` positionally — `put_wall = walls[0]` — but that helper
  **filters `None` out**, so a chain with strikes only above spot returned
  `[call_wall]` and the call wall was silently filed as the **put** wall, with no
  call wall reported at all. It feeds `rescue.assess_position_risk` /
  `strategic_context`, which judge whether a short strike sits past its barrier.
  Fixed at the consumer (the list contract is pinned by an existing test and the
  Gamma page draws that list as wall lines), using the picker's own contract as
  the disambiguator — put wall strictly below spot, call wall strictly above,
  exactly as `_matrix_dealer_levels` already documents. A lone wall with **no
  usable spot is now dropped rather than guessed**: a wrong-side wall is worse
  than no wall, and the flip still carries the context. Four tests, two of which
  failed first with the reported symptom (`assert None == 452.0`).
- **`webgui/pages/trade.py`'s docstring stopped lying** — it claimed
  "Fundamentals are not wired (MVP)" long after the proxy fundamentals landed. It
  now states what is true, *and* names the three Investor inputs that are
  structurally absent from `/instruments` (EPS surprises, guidance, FCF) and so
  score a permanent 0 — the reason a live Investor composite tops out near +59.5
  against a designed +74.5 with BUY at +40.
- Suites: options_svc **1222** (1218 + 4 new), trade_svc **79**, webgui
  unchanged.)

**Prior — 2026-08-21** (**Audit batch 4 — the fake bus stops lying, and two
discipline-only invariants become tests.**
- **The fake bus had different semantics from prod, and four modules could feel
  it.** Every `Bus(fake=True)` built its OWN `FakeStrictRedis`, so two Bus objects
  in one test shared nothing — while in production every Bus talks to the same
  Memurai. Measured: bus A writes a key, bus B reads `None`. That matters because
  **four production modules construct their own bus** rather than receiving one
  (`options_svc.compute._BRIEFING_BUS`, `trade_svc.compute._BUS`,
  `webgui/bus_client._bus`, `_scaffold`'s `the_bus or Bus()`), so a test that
  handed a handler its own fake bus and then exercised code reaching one of those
  singletons was reading an EMPTY cache and passing down the degrade path — the
  same shape as the documented "fake bus of bare dicts" incident.
- **The fix needed no conftest anywhere.** One `fakeredis.FakeServer` keyed on
  `PYTEST_CURRENT_TEST` (phase suffix stripped, so a fixture's writes are visible
  in the test body) gives both halves at once: shared within a test — pub/sub and
  streams now cross Bus instances exactly as in prod — and clean between tests,
  because pytest rewrites that variable per test. The alternative, an autouse
  fixture, would have meant editing ~15 suites that have no common conftest.
  **It immediately caught two tests whose premise was impossible in production.**
  `test_collect_gex_history_captures_viewed_symbol_chain` said it outright -
  `bus2 = Bus(fake=True)  # empty cache -> defaults to $SPX` - but in prod a new
  `Bus()` is NEVER empty; every one talks to the same Memurai. And
  `test_regular_window_detection_is_unaffected` looped over three clock times
  building a fresh bus each pass, so 08:15 only fired because it could not see
  the cooldown map 08:00 had just written. Both now create the clean-slate
  condition DELIBERATELY (`reset_fake_bus()`), which is what they always meant.
  Everything else across the affected suites still passes, so nothing legitimate
  was depending on the old isolation.
- **`pyrightconfig.json` — a deliberately narrow type check**, over `shared/bus`,
  `shared/contracts`, `shared/config_toml.py` and `webgui/bus_client.py`: the one
  seam every tier crosses, and where the envelope-vs-payload bug class lives.
  **Now clean at 0 errors.** ⚠ It found four things and **none was a bug** —
  `redis-py`'s stubs return `bytes | str` where `decode_responses=True`
  guarantees `str`. Fixed with `cast()` **plus a comment stating the invariant**,
  never a blanket ignore; the fourth was genuine sloppiness in
  `shared/config_toml.py`, written earlier the same day. Explicitly NOT widened
  to the repo: the services and pages are large, untyped, and full of
  deliberately loose payload dicts, so a repo-wide switch yields thousands of
  findings nobody actions — the failure mode ruff's minimal select list already
  avoids.
- **`webgui/bus_client.py` had ZERO annotations** while `shared/bus/client.py`
  was fully typed — so the seam was typed on one side only, and the untyped side
  is the one where `.payload` confusion bites. All 13 functions now carry
  signatures.
- **Two mirrors moved from discipline to test**
  (`shared/tests/test_cross_tier_mirrors.py`, AST-parsing the files and importing
  nothing, so it cannot itself trigger the cross-app `scoring` collision): the
  five **regime display words** duplicated across `driver_svc`, `options_svc` and
  the webgui, and the **manuals dual registration**. The manuals half is the
  interesting one — the existing webgui test checked catalog → built file, and
  the CONVERSE was unguarded: a manual that is built but never listed is silently
  unreachable, because that dict is also the serving whitelist.
- **The mirror pin was verified to actually fail.** Drift was injected into the
  webgui copy ("Whipsaw" → "Chop"); the test failed and named the file. A pin
  that has never been seen to fail is not evidence of anything — the same lesson
  batch 1 learned from three `TestEarningsAvoidance` tests passing for the wrong
  reason.
- **`scoring/_common.py` absorbed `clamp` and `num`.** Measured by AST with
  docstrings stripped: **nine byte-identical `_clamp` copies and seven `_num`**
  (six identical plus one differently spelled, verified equivalent across 20
  inputs — None/''/NaN/inf/bool/bytes — before folding it in). ⚠ This is NOT the
  thing root CLAUDE.md warns against: that warning is about changing `_clamp`'s
  NaN *semantics* centrally, and this body is byte-identical to the nine it
  replaced. The NaN policy stays at the call sites, because only the caller knows
  whether a missing input means "neutral 50", "floor the magnitude" or
  "confidence 0". `_finite` was deliberately left alone — three functions share
  that name and `momentum_regime`'s takes an ITERABLE, so hoisting it would hand
  someone the wrong one silently. A test records that reasoning so a later
  tidy-up does not "finish the job".
- **`pytest` now defaults to `-rf`.** "Compare the failing SET, not the count"
  stops being something you have to remember to ask for. Verified that every
  per-app run resolves the root `pyproject.toml` as its configfile, so it applies
  from inside `webgui/` and `options-scanner/` too. `-rfs` was rejected: the
  suites carry permanent `importorskip`s and printing them every run trains
  people to ignore the summary.)

---

**Last updated:** 2026-08-21 (**Audit batch 3 — four config files, and the test
that proves a constant actually moved.**
- **The selection rule for what became config.** Every one of the four exists
  because the value was **duplicated across modules that cannot import each
  other** — which is the difference between a config file that DEDUPLICATES a
  constant and one that merely relocates it. Values with a single consumer stayed
  in code.
- **`config/driver.toml`** — the driver's risk envelope. `driver_svc.settings`
  (guardrails) and `options_svc.compute` (the paper sizer) each held `3000.0`
  under a comment reading "must stay in sync". When those disagree the failure is
  silent and baffling: the driver approves a quantity the sizer then zeroes to
  RISK_TOO_HIGH, and the log says "Executed" while nothing opened.
- **`config/trade_mgmt.toml`** — the stop rules. `options_svc/rescue.py` opened
  with "Mirror signal_recommender stop constants" and then restated four of them.
  `rescue_thresholds()` now **derives** those four from `[stops]`, so the mirror
  is structural rather than clerical; the TOML deliberately does not list them,
  and a test fails if anyone adds them back (they would look authoritative and be
  ignored).
- **`config/scanner.toml`** — the selection floors, the block with dated
  "2026-06-11 quality retune" comments in the source, and the documented reason
  index names rarely fire. Read by `scanner_engine`, `signal_recorder` and
  `options_svc/compute`.
- **`config/symbols.toml`** — the traded universe, previously **four** literals:
  the collector's poll list, the Net-Prem groups, the BIG10 basket, and a
  byte-copy of the groups in Tier-1 `gamma.py` carrying a comment explaining that
  Tier 1 may not import `services.*`. **Reading a config file is not a `services`
  import** — `theme.toml` is the standing precedent — so `shared.symbols` joins
  `shared.market_calendar` on the Tier-1 allow-list and the copy is gone rather
  than policed. The API-budget warning that justified the collection list moved
  into the TOML header, where someone about to add a ticker will actually read it.
- **`config/sessions.toml` gained `[slots]`** rather than a fifth file: the
  scheduled Claude briefings, the thrice-daily action digest and the nightly
  momentum cascade are named clock marks, which is exactly what `[windows]`
  already models. **Each `analyze` slot is a paid Claude call**, so that table is
  the direct control on that spend — delete a line, drop a briefing.
- **`shared/config_toml.py` — one loader instead of six.** `flow_alerts` and
  `sessions` had each grown their own ~40 lines of mtime-cache + deep-merge +
  degrade-to-defaults; adding four more files would have made six copies.
  `toml_loader(path, defaults)` returns `(load, reset)`. ⚠ Documented rather than
  over-promised: `load()` hands back the CACHED mapping, so a config dict is
  **read-only by convention** — copying on every hot-path read would defeat the
  cache. The test that first claimed otherwise was corrected to assert what
  actually matters, which is that the module-level DEFAULTS can never be poisoned.
- **The test shape is the transferable part.** A test asserting
  `settings.PER_TRADE_MAX_RISK == driver_limits.per_trade_max_risk()` **passes
  before the code is wired** — the literal it replaces has the same value. The
  first draft of every one of these extractions was green against unwired code.
  The discriminating test monkeypatches the accessor and `importlib.reload`s the
  consumer, so it fails unless the module genuinely reads the file. Same family as
  batch 1's stale fixtures: equality with a constant proves nothing about where
  the constant came from.
- **Shapes were preserved on purpose.** TOML yields lists and flat tables, but the
  engines index `MIN_CREDIT_PCT["0-DTE"][regime]`, unpack tuple delta bands,
  compare a tuple `SINGLE_LEG_EXCLUDED_GRADES` and iterate tuple-of-dict
  `netprem_groups`. The shared modules convert at the boundary rather than making
  every call site change — the extraction is invisible to the code that uses it,
  and every value was verified byte-identical to the literal it replaced before
  anything was committed.
- Also worth recording: **`pytest services` over all six folders at once still
  breaks** with 10 collection errors, exactly as CLAUDE.md's Tests section warns —
  multiple hyphenated app dirs on `sys.path` re-trigger the `config`/`scoring`
  collisions. Run them per folder.)

---

**Last updated:** 2026-08-21 (**Audit batch 2 — a swallowed exception now leaves a
trace, and `/health` counts them.**
- **The problem, measured.** An AST census of every `except Exception` in
  `services/` + `webgui/` (non-test) found **542**, of which **289 were silent** —
  no log, no re-raise, just a plausible default returned. That is the exact shape
  behind all five NaN incidents. The worst wrapped **294 lines** of
  `sentiment_svc.compute_intraday_trend` and returned `_neutral_trend()`, so any bug
  in the entire trend computation rendered as a calm neutral reading with nothing in
  `logs/sentiment.log` to say so.
- **The fix is scoped by SIZE, and that is the interesting part.** Splitting the 289
  by how much they guard turns an unmanageable sweep into a small one:
  **41 guard >= 15 lines** (they swallow a whole computation) and **248 guard < 15**
  (one-statement parse guards like `try: return float(x) except: return None`).
  Only the 41 were touched. Logging the 248 would put a WARNING on every failed
  float parse, per row, per tick — spam rather than observability, and there the
  missing-value contract IS the point rather than a failure. The audit's headline
  number was "276 sites"; the useful number was 41.
- **`services/_degrade.py`** — `degraded(area, *, detail=None)` logs at **WARNING
  with a traceback** and increments a per-area counter. WARNING and not ERROR
  deliberately: most of these fire on real, expected conditions (a symbol with no
  chain off-hours), so ERROR should keep meaning "look now". **The counter is the
  signal; the log line is the detail** — one degrade is noise, 340 in a session is a
  bug. No heavy imports, because `compute` modules import it and it must not drag in
  FastAPI or the Bus.
- **It is visible, not just recorded.** `_scaffold`'s `/health` gained
  `degrades_total` + `degrades` (additive; `domain`/`up` unchanged), and the Status
  page renders it on the service card as `healthy - 12 degraded`
  (`status.service_detail` — zero stays a plain "healthy", and a service predating
  the counter or a garbled value degrades to "healthy" rather than breaking the
  card). The page already fetched `/health`, so this costs **no new probe**.
- **Applied to 40 handlers across 8 modules** by an AST transformer doing line-based
  insertion — *not* `ast.unparse`, which would have reformatted every file and
  thrown away every comment. Area labels are `<domain>.<function>`, derived from the
  enclosing scope. ⚠ One placement needed hand-fixing: `options_svc/compute.py` has
  a top-level import at line 6291, so the auto-inserted import landed at 6292. Not a
  runtime bug (module-level names resolve at call time) but wrong, and moved up
  beside `from services import _proxy`.
- **`services/tests/test_no_silent_degrades.py`** pins it: no NEW silent guard may
  swallow >= 15 lines. It carries its own "the scan actually reaches the code"
  canary, because a source-walking guard that silently walks nothing passes
  vacuously forever.
- **ruff `BLE001` was considered and rejected**, against the audit's recommendation.
  It flags every `except Exception` — all 542 — so adopting it means ~542
  grandfathered `noqa` comments, which dilutes the signal to nothing, and it
  contradicts the standing rule that a rule class is added only once the tree is
  already clean under it. The AST guard test pins the invariant that matters at a
  fraction of the noise. (Worth knowing: the `# noqa: BLE001` comments already
  scattered through the code are **decorative** — `BLE` is not in the select list,
  so nothing has ever checked them.)
- **`webgui/logging_setup.py`** — Tier 1 had **no log handler at all**. `main.py`
  logs through `logging.getLogger("webgui")` throughout, but output went to the
  console and died with the Windows Terminal tab; only the nowindow launcher
  captured it, by shell redirection. Now a rotating `logs/webgui.log` beside the six
  services' logs. A deliberate ~30-line copy of
  `_scaffold._install_file_logging` rather than an import — the Tier-1 allow-list
  has no `services.*`, and that helper pulls in FastAPI and the Bus.
- **One audit finding refuted.** `webgui/pages/status.py:365` was flagged as a large
  silent guard; it is a **false positive**. The handler surfaces the failure **in the
  UI** as `unreachable (ConnectionError)`, which is strictly better than logging it.
  The census flagged it only because the handler contains no `log.` token — a
  reminder that "does it log" is a proxy for "does it tell anyone", not the thing
  itself.)

---

**Last updated:** 2026-08-21 (**Audit batch 1 — the "permanent" test baseline was
a fiction; NaN in the validation study; Tier-1 loses its last engine glue.**
- **The 8-11 "permanent baseline failures" were all stale fixtures.** Every suite in
  the repo now runs clean — options-scanner **1180/0/2**, sentiment-dashboard
  **507/0/1**, sentiment_svc **328 + 1 xfail**, options_svc **1218/0**, trade_svc
  **79/0**. They had been labelled "timing-dependent" and "stale fixtures — do not
  fix", so nobody looked; on inspection each one pinned a constant that had since
  moved. Four `test_next_boundary_*` pinned a 2-minute cadence after
  `POLL_INTERVAL_MIN` went 2 → 1 on 2026-07-11;
  `test_main_skips_before_market_open` used 8:20/8:25 as "before the open" after the
  window moved 8:30 → 8:00 CT; `test_acquire_defers_when_fresh_other_owner` used
  +200 s, inside the old `LOCK_TTL_SEC` of 240 and outside the derived 120; the two
  `TestEarningsAvoidance` cases used absolute 2026-05 dates that drifted into the
  past. All are now **derived from the constant or relative to today**, the same
  reasoning `gex_collector.py` already applied to `LOCK_TTL_SEC`. **Nothing was
  xfail-ed to hide a failure.**
- **The two costs of a red baseline, both realised.** A **9th** failure
  (`test_per_leg_expiry_back_leg_retains_value_at_front_expiry`, whose "far" back-leg
  expiry of 2026-08-21 simply arrived) appeared the morning of the audit and was
  invisible against an expected count of 8 — the exact failure mode the
  "compare the SET, not the count" rule exists to catch, one level up: the rule was
  being followed against a *stale* set. And **three of the five
  `TestEarningsAvoidance` tests were PASSING for the wrong reason** — once every
  fixture date is in the past they all take the same early-out, so a green test over
  a stale fixture asserted nothing.
- **The two genuinely-unpassable tests are now explicit**, not failures:
  `test_apply_sector_perf.py` gets a module-level `importorskip` (it exercises the Tk
  entrypoint this fork deliberately never copied), and the real `$VIX1D`
  session-latch bug gets **`xfail(strict=True)`** — fixing it now FAILS the run until
  the marker is removed, so the xfail is a tracked bug rather than a hidden one.
- **`daily_direction._num` accepted NaN** where its six siblings reject it, so a NaN
  bar close reached `_clamp` and pinned a bound. Measured: an **all-NaN close series
  scored 66.67** — moderately bullish, from no data — and one NaN close dropped a
  maximal uptrend 100.0 → 83.33. It leaked into the study's own metrics too:
  `per_state_stats` reported `mean=nan` at `n=2`, and `ordinal_ic` ranked THROUGH the
  NaN (0.9856 where the clean pairs give 1.0). **Offline-only** — the module feeds
  `validate_market_state.py`, never a service or request path — so the blast radius
  is the five-state validation study's numbers, not a live gauge. Same family as the
  2026-08-20 round; the guarded list keeps proving to be a map of where someone has
  looked.
- **`cache:options:matrix` is now actually validated.** Root CLAUDE.md and the
  2026-07-20 design both said `MatrixSnapshot` gated it; in fact the model was used
  **only in its own unit test** while both publish sites wrote unvalidated — from
  2026-07-20 until now. Both now go through one `_cache_matrix(bus, view)` helper.
  ⚠ **Wiring it naively would have broken two screens**: the gate caches
  `model_dump()`, so a top-level key the contract omits is a key the pages lose, and
  `matrix.py` renders `payload["error"]` in its status line. Verified against the
  **live prod payload (92 rows)** — zero keys lost — before switching it on.
- **The gate immediately caught a real shape mismatch**: `session_date` is a
  `datetime.date` OBJECT in memory (straight from `scheduler.active_session_date()`),
  not the `str` the contract declared. `json.dumps` had been stringifying it on the
  way into Redis, so the wire format was always right and the annotation looked
  right — the mismatch could only surface once the payload was validated *in memory*.
  `MatrixSnapshot` now normalises date/datetime to isoformat, keeping the cached
  bytes byte-identical (which is what `skip_unchanged` compares).
- **Tier 1 lost its last `sys.path` glue into a hyphenated app folder.**
  `webgui/proxy.py` built `SchwabProxyClient` / `SchwabPyProxyClient` singletons at
  import; **nothing had called them since the 3-tier migration** (every runtime use
  is `health()` / `api_call_stats()`, both plain `requests.get`). Deleting them
  completes "remove the last sys.path engine-glue from webgui" from the 3-tier plan.
  A **source-level** guard in `test_proxy.py` keeps it gone — an attribute check
  alone would not catch the sys.path insertion coming back.
- **`trade_svc/deepdive/engine.py` lost its `--direct` mode** — it read `tokens.json`
  itself and called Schwab's API host, bypassing the proxy. Unreachable in-service
  (compute.py builds `SchwabClient()` with no args), but a credential-reading path
  inside `services/` contradicts "the proxy is the only Schwab gateway", and the
  refresh token is a single rotating credential. Its guard test parses the file with
  **`ast` and strips docstrings** before checking, because the docstring explaining
  the removal necessarily names the things being banned.
- **`validate_directional_gate.py` no longer hard-codes `D:\WebGUI Trading with
  Schwab`** — run from prod or a worktree it would silently resolve the *dev*
  checkout's `repo_paths`, and therefore the dev DBs and ports.
- **`yfinance` dropped** (declared in `requirements.txt` as "still imported by some
  analysis paths"; **zero imports repo-wide** — its consumers went with the
  2026-08-20 Blueprint deletion), along with its three orphaned transitives
  `curl_cffi` / `multitasking` / `peewee`. ⚠ **`Flask` was NOT removed** despite the
  audit flagging it as pip-freeze cruft: `pip show` says `Required-by: schwab-py`, so
  it is a legitimate transitive of the Schwab SDK. Verify before pruning a lock.
- **Two per-app `CLAUDE.md`s were describing codebases this repo does not have**, and
  they auto-load into every session that edits those folders.
  `options-scanner/CLAUDE.md` (470 → 207 lines) still opened "Ships two interfaces …
  a Tk desktop app and a FastAPI + React web app" and gave commands for
  `dashboard.py`, `scanner.py`, `eod_report.py`, `uvicorn server.main:app` and
  `npm run dev` — **all eight of those paths verified absent**. Its appended "Options
  Simulator" section described a Dash app with `viz/`/`engine/`/`data/` packages and
  a plotly/dash/py_vollib/mibian stack; the real module is **three flat files** and
  the app charts with Highcharts. `sentiment-dashboard/CLAUDE.md` still opened "A
  tkinter desktop app" and named `D:\AI_Based_Analysis\shared\sentiment_bridge.json`
  as the canonical bridge path.
- **The Tier-1 import allow-list is now stated exactly**, because the familiar
  shorthand was wrong on its last term: the webgui imports **`shared.contracts`
  nowhere at all**. Measured across all 153 non-test webgui files — `nicegui`,
  `shared.bus`, `shared.market_calendar`, `repo_paths`, `requests` (health only),
  `fastapi.responses`, and the lazy `edge_tts`. Recorded alongside it: contracts are
  a **write-side gate on ~15 of 74 `cache_set` sites**, not the typed API both tiers
  share — ~50 views are read by the GUI, 18 models exist, and read-side validation
  does not exist at all. Documenting a view as "validated by X" does not make it so.)

---

**Last updated:** 2026-08-21 (**The Desk speaks the CONTRACT, not just the cause.**
- **What shipped.** The spoken alert now names the option. `uoa`/`big_delta` carry a
  strike and an expiry, so they say them — *"N D X. Unusual activity, 0-D T E 7 15
  Put."*, *"A M D. Big delta, 8 - 28 4 72. point 5 Call."* — with the word "alert"
  dropped and the side moved after the strike it belongs to. A new position adds
  strikes, expiry and entry: *"S P Y. New position, put credit spread. 2 07. point
  5, 2 05, 8 - 31, entry 56 cent credit."* `crossover`/`gamma_flip` carry no
  contract and are **unchanged**.
- **The number rule was settled by LISTENING, not by argument.** Leading digits
  singly, the last two as a pair, `00` → "hundred", a fraction → ". point 5": 205 →
  "2 05", 4500 → "4 5 hundred", 207.5 → "2 07. point 5", 21500 → "2 1 5 hundred".
  A neural voice reads "205" as *two hundred and five*; a trader hears *two oh
  five*. Twelve cases are pinned in `test_voice.py`. `say_number`/`say_expiry`/
  `say_entry`/`say_strikes` all guard through `pages.fmt.num`, so NaN, infinity,
  `""` and — the documented one — `bool` return `""` rather than a number nobody
  supplied.
- **`say_entry` lets the SIGN pick the word**, because the paper book stores a debit
  as a **negative** `entry_credit`: `0.56` → "entry 56 cent credit", `-1.25` →
  "entry 1 dollar 25 debit". A debit announced as a credit was the most expensive
  sentence this feature could have said.
- **Shorter, never half.** The two forms are chosen on the PARSED values, not on the
  alert kind, so the contract path and the degrade path are one line of code and
  cannot drift. A missing or unreadable strike/expiry/entry falls back to the
  existing short form rather than emitting a sentence with a hole in it.
- **`flow.alert_rows` gained `strike`/`expiry`/`dte`** — additive, no column
  declares them — so the Desk still composes off the row the Flow Alerts page
  builds instead of becoming a second reader of the raw payload.
- **The prewarm halved, 8 pairs → 4.** `FLOW_CAUSES` is now DERIVED (`_ALL_CAUSES`
  minus `CONTRACT_KINDS`). A uoa phrase's space is the whole option chain, so
  warming those pairs synthesized clips that could never be played — half the
  prewarm's network and disk, spent on nothing.

**Prior —** 2026-08-21 (**The Desk speaks — spoken arrival alerts + a
10-second neon glow, and the two traps that make both of them work.**
- **What shipped.** A new flow alert or a newly-opened position on `/desk` is now
  **announced out loud** — ticker spelled squawk-style, then the cause ("S P Y.
  Crossover alert, calls over.") — and the **row glows neon for 10 seconds**, so
  the voice and the eye land on the same place. Nothing else changed: the page is
  still read-only, still composes rather than re-derives, and the scanner chime is
  untouched.
- **`edge-tts`, and the alternatives were measured, not assumed.** Free Microsoft
  neural voices, no API key, synthesized **server-side** and cached permanently on
  disk. Windows SAPI was ruled out because this machine has **no Windows neural
  voice installed** — `System.Speech` reports only the old David / Zira / Mark
  concatenative voices — and browser `speechSynthesis` was ruled out for the same
  reason one layer up: in Chrome on this box it falls back to those same SAPI
  voices, so the feature's core quality would depend on which browser the launcher
  happened to open. Default **`en-US-AriaNeural`** (picked from a listening test of
  all six offered, not alphabetically), rate **+8%** — past about +15% the spelled
  tickers slur. `webgui/voice.py`; `edge-tts>=7.0` added to `requirements.txt`.
- **`big_delta` speaks, and that deliberately breaks the quiet-live rule.**
  `alerts.py` excludes it from the chime/toast set because a *chime* carrying no
  information is pure noise at that detector's frequency. An announcement that
  names the ticker and the cause is not — the cost of ignoring it is zero. So all
  **four** flow kinds speak. ⚠ Do not "fix" this into consistency with the chime
  without revisiting the reasoning.
- **What speaks vs what only glows.** A newly-opened position speaks. A position
  whose **flag moves** (OK → AT RISK → RESCUE) glows **amber** and stays silent —
  it was already in the book, and the FLAG column already prints the new word.
  New rows glow **cyan**. A burst announces the **newest only, plus a count**
  ("…Plus 5 more."), one utterance per panel per paint, so a tick is bounded at
  two clips. Detection runs over the **full** alert list, not the five rows the
  panel draws, or a burst's arrivals would announce themselves later when the list
  shortened. First paint seeds every set **silently** — navigating to the Desk must
  not read out the day's backlog.
- ⚠ **TRAP 1 — a rebuilt element restarts its CSS animation from zero.**
  `_paint_positions` clears and rebuilds every row whenever the paper account
  re-prices, which is constant during market hours, so the naive glow **never
  expires**: every repaint resets the decay. The fix is that the glow's start time
  lives in page state keyed by row id, and each row wears one of **ten static
  classes** `desk-neon-0…9` carrying a whole-second **negative `animation-delay`**,
  so a rebuilt row **resumes** the animation instead of restarting it. Ten fixed
  classes rather than a computed `[animation-delay:-3.2s]` — the styling standard's
  finite-set rule; the cost is one second of granularity on a ten-second decay.
- ⚠ **TRAP 2 — browser autoplay refusal is completely silent.** `play()` simply
  rejects; nothing appears in any log, so a tab left alone announces nothing and the
  feature looks broken rather than blocked. The rejection is now caught and surfaces
  an **ENABLE SPOKEN ALERTS** chip in the Desk header, hidden until a block is
  actually reported; the click that dismisses it *is* the gesture that unlocks
  audio, and it speaks one line back so the unlock is audibly confirmed. A blocked
  attempt **clears** the queue rather than holding it — audio may unlock minutes
  later, and a backlog replayed then would announce a market that has moved on.
- **Its own `<audio>` element, and a queue.** `desk-voice`, not `main.py`'s shared
  `alert-audio`: whichever source assigns `src` last wins, so a scanner chime —
  which fires from the app-wide watcher on **every** page, this one included — would
  cut an announcement off mid-sentence. `el.onerror = next` sits beside
  `el.onended` deliberately: with only the latter, one 404 leaves `busy` true
  forever and the tab never speaks again.
- **The cache.** `sha1(voice|rate|text)` → `webgui/data/voice/<hex>.mp3`, mounted at
  **`/voice`**, **gitignored** (generated artefacts stay out of `static/`). Measured:
  synthesis **~0.9–2.4 s** on a miss, **~110 µs** on a hit, **~22–28 KB** a clip. The
  whole utterance including the burst tail is ONE clip, so the key is the full
  sentence — two concatenated clips would make the cache `O(tickers + causes)`
  instead of `O(tickers × causes)` at the cost of an audible seam. A background
  **prewarm** (once per process, daemon thread, flow phrases only — eight causes per
  watchlist symbol) warms it at startup; a position arrival is something you just
  did, so paying first synthesis there costs nothing.
- **Nothing on `voice.py`'s public surface raises, and that is categorical.** No
  network, no `edge_tts`, an unwritable cache dir, a lone surrogate off a malformed
  payload, a non-iterable symbol list — all degrade to `None`/`[]`, which the caller
  reads as "no speech this tick". The row still glows, the chime is unaffected, and
  the warning is logged **once per process**, not once per 2 s tick. The synthesis
  wrapper carries its **own 20 s bound** (`asyncio.wait_for` inside the loop, so a
  timeout genuinely cancels) because edge-tts's timeouts are per-operation — a
  stream dribbling one chunk every 59 s trips neither.
- **Settings.** A **Spoken alerts (Desk)** card: enable switch, the six en-US neural
  voices, a volume slider, and a **Test voice** button that doubles as the audio
  unlock. Three new `app_settings` keys — `voice_enabled` (True), `voice_name`
  (`en-US-AriaNeural`), `voice_volume` (0.8). The existing
  **`alert_market_hours_only`** gate is **honoured, not duplicated**: a desk that
  talks at 3 a.m. is a bug, and a second market-hours switch beside the first is a
  drift hazard.
- **The spoken vocabulary is the printed one.** `flow_phrase` reads `kind`/`side`
  straight off the row `flow.alert_rows` built, and `voice.FLOW_CAUSES` — restated
  rather than imported, because `voice` must stay importable with no `pages` package
  on the path — is pinned against `flow._TONE` by a test. A spoken vocabulary
  drifting from the printed one would be the documented sectors-vs-rotation split in
  a new place.
- **Verification.** webgui **2470** green (was 2358), including 47 new `test_voice`
  cases and the extended `test_desk`. Docs: CHANGELOG, `webgui-routes.md`,
  `page_help.py`, the User Guide + Reference Guide, and a Constants Appendix entry in
  the Technical Reference. Design:
  [`2026-08-21-desk-voice-alerts-design.md`](plans/2026-08-21-desk-voice-alerts-design.md).

**Prior —** 2026-08-20 (**The tidiness sweep — measured first, and it was a
third the size claimed.**
- **The estimate was wrong, so the sweep started with a measurement.** The audit
  reported "~60 formatter clones, 200-300 lines". Grouping webgui page functions by
  NORMALISED AST BODY — identical code, not merely identical names — gives
  **11 clone groups / 32 defs / ~123 removable lines**. The gap is entirely
  same-named functions that differ (six distinct colour helpers all called some
  variant of "lerp"), which is why the count needed deriving rather than trusting.
- **`pages/fmt.py` is the shared numeric vocabulary now.** `num` (the strict "is
  this a real reading" coercion) had **six** byte-identical copies whose own
  docstring recorded the fact; `clamp` had three, `round_or_none` four, and the
  permissive `float_or` four more under three different names and three different
  defaults. All aliased, so no call site changed.
- ⚠ **`num` and `float_or` are deliberately different and the tests say so.**
  `float_or` preserves whatever `float()` produced — including NaN and bool —
  because it is a coercion with a fallback; `num` answers None for both because it
  answers "is this a reading". After a day of NaN findings, collapsing them into
  one permissive helper would have been the wrong tidy-up, so the divergence is
  pinned by a test that asserts it.
- **A test that asserted the duplication was replaced by one that asserts the
  fix.** `test_num_body_is_identical_to_every_sibling_its_docstring_names` compared
  six bodies because prose claims aren't enforced (it cites the RISK_FREE_RATE
  incident by name). With the copies gone, identity replaces comparison: the pages
  do not merely AGREE, they are the same object.
- **`rescue_highlight` + `_AT_RISK_STATES`** moved beside the `heat_border_class`
  they already delegated to, removing three copies of each.
- **The 35-branch `elif` chain was NOT converted to a dispatch registry** — the
  bodies are heterogeneous (2 to 20 lines), so it is 35 extractions and 35 chances
  to mis-pass an argument, for no behaviour change. Its real hazard was the 43-line
  docstring restating every branch in prose: **`gamma_history`, `rescue_adhoc` and
  `sim_replay` were already implemented and undocumented.** Documented, and two
  tests now fail on drift in either direction — adding a branch without a line is a
  red suite.
- **L6 (the "colour-helper spread") is largely a false positive.** Of the six,
  `sector_heat._lerp(pair, f)` interpolates SCALARS, `_hex_rgb`/`_mix`/`_rgba` are
  three different conversions, and `gauge._lerp` vs `svg._lerp_color` differ by a
  clamp that is a real behavioural difference. Nothing merged; reported instead.
- **Stopped at 7 groups / 16 defs / ~41 lines remaining, deliberately.** Every one
  is a 2-copy group of 3-8 lines spanning different packages, and the clearest
  (`pnl_color`/`pnl_class`) would mean relocating palette constants used 11 times
  in `paper.py` to save 8 lines in `driver.py`. Diminishing returns, measured.
- **Verification.** webgui 2358, options_svc 1216, sentiment_svc 328 + the
  documented 1, tools 819, shared 93. Ruff clean tree-wide.

**Prior —** 2026-08-20 (**Batch 4 — consolidation: one calendar, one P/C,
one advisory core, one poll idiom, and a contract that finally validates.**
- **The `tools/` holiday literals are gone.** `flow_delta_instrumentation.py` and
  `nq_signal.py` each carried a hardcoded 2026-2027 NYSE frozenset — silently wrong
  from 2028 — justified by a comment claiming an import would drag `compute` and
  `handlers` in. That named the wrong module: `shared/market_calendar` is
  deliberately import-light (**measured: no pandas/numpy/redis/fastapi at all**) and
  derives holidays algorithmically. Both now import it; verified against 2028-2030
  dates the literals never covered. `nq_signal.is_trading_day` also accepts a `date`
  OR a `datetime` — callers pass both, and taking only one is how a session gate
  silently stops gating.
- **`pcr_from_chain` was two byte-identical copies** feeding the SAME composite
  (`sentiment_svc/compute.py` and `live_composite.py`), so a threshold tweak in one
  would have diverged the live P/C from the sector table's. Homed in
  `scoring/put_call.py`; a test asserts both tiers now reference the same function
  object, not merely agree.
- **The three ad-hoc rescue builders shared a 30-line tail differing by exactly TWO
  lines** — which candidates function, which risk function. Injecting those two is
  the whole difference, so `_assemble_advisory` now owns regime → engine →
  validated `RescueAdvisory`, and a new strategy family needs a mark builder and
  nothing else. The `_flt` spec validator, defined byte-for-byte three times, is now
  the module-level `_spec_float`.
- **The version-gate poll idiom is a helper now** (`pages/view_watch.watch_view`):
  seed the version, probe the cheap `:ver`, repaint only on movement, hang it on a
  timer. **Converted 4 of the 22 pages** — the sentiment screens that share the
  canonical shape. The rest (Gamma's coalesced `read_versions`, the Calculator's
  three timers, Rescue's four) are genuinely different shapes and were left alone;
  a blanket regex sweep is exactly how the decorator above `_maybe_repaint` gets
  orphaned, which happened on the first attempt and was caught by ruff.
- ⚠ **One test of the new helper asserted the wrong thing and was corrected.** It
  originally required `watch_view` to swallow ANY repaint exception. That is the
  degrade-guard antipattern this repo bans — NiceGUI logs and keeps ticking anyway.
  It now asserts a real error PROPAGATES and only the deleted-client case is
  absorbed, which is what `ui_guard` is for.
- **`MomentumSnapshot` finally validates something.** The contract carried real
  validators (chiefly `_exactly_three_levels`) and NOTHING used them — the publisher
  shipped a raw dict, so it was documentation rather than enforcement. Wired in at
  the publish site: a ragged cascade now leaves the last good snapshot up instead of
  blanking the Bull/Bear map and the momentum page.
- **Verified live in dev, not just green:** republished `cache:sentiment:rotation`
  with a doctored spread and watched `/sentiment/rotation` repaint **−1.69 → −2.55
  with no reload**, then restored from the engine and watched it pick up **−1.32**
  on its own. `webgui.err.log` stayed empty throughout.
- **Verification.** options_svc 1214, webgui 2341, options-scanner 1172 + the
  documented 8, sentiment-dashboard 502 + the documented 2, sentiment_svc 328 + the
  documented 1, tools 819, driver_svc 239, portfolio-analyzer 197, market_svc 77,
  trade_svc 77, portfolio_svc 32, shared 93/28/49, tests 69. Ruff clean tree-wide.
- **Left for a later pass, deliberately:** the ~60 formatter clones (a mechanical
  sweep across many pages, pure tidiness, real regression surface — the same shape
  of risk that just orphaned a decorator), `handle_command`'s 35-branch elif chain,
  the color-helper spread, and the remaining 18 poll sites. The `compute.py` split
  (7,354 lines) remains its own decision.

**Prior —** 2026-08-20 (**Second-pass batch 3 — one 10 GB/day win, and one
"fix" the measurement told me not to ship.**
- **The watcher was moving 10.2 GB/day/tab.** `_watcher_compute` read
  `options:scan` (148 KB) + `options:flow_alerts` (90 KB) **unconditionally** on
  every 2 s tick, per open tab — 43,200 ticks/day of transfer and JSON parse for
  views that change a handful of times an hour. New `bus_client.read_gated(view,
  memo)` pays a tiny `:ver` probe instead. **Measured against prod: 3.16 ms →
  0.32 ms per tick (−90%); 10.2 GB → 0.7 MB moved, 2.3 min → 0.2 min of CPU, per
  tab per day.**
- ⚠ **`read_gated` does NOT gate a versionless key.** First cut skipped the
  payload read whenever `:ver` was absent, which would make a pre-upgrade key
  permanently invisible; second cut memoized it, which is worse — a memo keyed on
  `None` has no invalidation signal and would serve that first payload forever.
  It gates only when there is a version to gate on. A shell test caught both.
- ⚠ **M7 was measured and REJECTED — the audit's estimate was ~10x high.** The
  Desk's 11-view seed was reported as "~50-100 ms of loop block"; measured against
  prod it is **10.7 ms**, only 6.2 ms of it parse. Deferring it off-loop buys ~10 ms
  and costs a fill-in flash on the landing page. Pipelining the round-trips is
  *slower* (**12.34 ms vs 11.25 ms** — on localhost the round-trips are free and the
  pipeline setup is not). Both attempts were written, measured, and reverted rather
  than shipped; `/eod`'s on-loop build falls to the same arithmetic. **No change.**
- **Accuracy Lows.** Closing an IRON CONDOR is four legs, not two —
  `commission_for(2, ...)` understated every IC close by $0.65 x 2 x qty against
  the adjustments it is ranked against (`_close_legs`). `score_vix_context` fed an
  absent `$VIX1D` in as a literal 0 and still claimed confidence 1.0, structurally
  shrinking the sub-score's deflection 20% — and `$VIX1D` does not quote for this
  account, so that was a standing bias, not an outage case; it now renormalizes
  over the terms present and carries the absence in the confidence (0.8), the same
  split `vix.score_complex` got this morning. And `annualize_return` refuses below
  `MIN_ANNUALIZE_DAYS` (21): a first-day +2% annualized to ~14,500% and a −2% to
  −99%, driving BOTH the Sharpe-like risk grade and capital efficiency, so a new
  position's first wiggle swung two of four dimensions between A and F on noise.
- **Bounded growth.** `_SIM_SNAPSHOTS` (a full ChainSnapshot per symbol ever
  fetched, ~1-10 MB for $SPX/$NDX, no eviction) is capped at 4 with re-fetch
  moving a symbol to newest, so the one you are actively simulating is never the
  one evicted. And `cache_set` gained a `ttl` — applied to the payload AND its
  `:ver`/`:ts` side keys, since an un-expired counter outlives its payload as an
  orphan — used by the per-position rescue boards (37 in prod, one per rescued
  trade forever, no delete API on the bus). The rolling rescue SUMMARY does not
  expire.
- **The prod `cache:test:dot:ver` key: investigated, not a live leak.** Its payload
  key is gone (an orphan `:ver` counter, 1 byte), no writer exists anywhere in the
  current tree, and `Bus()` selects fakeredis under pytest — so it predates
  something already removed. Left in place rather than mutating prod Redis.
- **Stale comments corrected**: three `~14 MB` references to `cache:options:gamma`
  in gamma.py, which is 0.4 MB since the history split — and that number is what
  justifies the in-flight guard, so a future reader would mis-size the tradeoff.
- **Verification.** options_svc 1209, webgui 2334, sentiment-dashboard 498 + the
  documented 2, sentiment_svc 325 + the documented 1, options-scanner 1172 + the
  documented 8, portfolio-analyzer 197, driver_svc 239, market_svc 77, trade_svc
  77, portfolio_svc 32, shared 93/49, tests 69. Ruff clean tree-wide.

**Prior —** 2026-08-20 (**Second-pass batch 2 — the first deletion stranded
5,600 more lines, and four calculations that flattered themselves.**
- **The dead-code deletion cascaded.** Batch 3's removal of the Tk window and the
  legacy CLI left **1,848 lines unreachable inside the still-live `gamma_tool.py`**
  — the Explain-text family, the drift-pressure panels, the full Analyze prompt,
  the slot/today's-path helpers and every matplotlib/Tk remnant. Verified with an
  independent AST reachability run (roots = production callers + module-level code):
  51 dead defs of 83, and the same 9 live entry points the audit found. Dynamic
  access (`getattr`) and string references were checked separately — none. With
  `theme.py`, `event_calendar.py`, `html_render.py`, `options_simulator/ai_prompt.py`,
  two broken `tools/` scripts and their tests: **5,624 lines across 34 files**.
- **Two side effects worth more than the lines.** `gamma_tool` now imports with
  **zero** tkinter/matplotlib modules (verified in a fresh interpreter), and the
  options-scanner permanent-failure baseline shrank **11 → 8**: `test_key_levels_doc`
  asserted a `docs/KEY_LEVELS.md` that does not exist in this repo. The Tk-root
  skip race also lost 2 of its 3 racing files, so `skipped` is a stable 2 rather
  than a random 2-or-3.
- ⚠ **Deleting from a live module is not a bulk operation.** Nine test files mixed
  live and dead subjects and had to be pruned test-by-test rather than removed; a
  cleanup regex whose `\s*` crossed newlines ate the opening of two parenthesised
  imports (caught by the suite, restored). Every claim was re-derived locally
  before anything was removed.
- **IC probability was the better leg, not both.** `pop_pct = min(put, call)` — but
  an iron condor loses if EITHER short breaches and the two breaches are DISJOINT,
  so it is `put + call − 100`, floored at 0. Two 20-delta shorts read **80%**
  against a true **~60%**, inflating `calc_expected_pnl`'s EV and the scoring PoP
  factor for every IC the scanner has ever ranked.
- **The width selector overrode the account risk cap.** `if contracts <
  CONTRACT_ROUND_TO: contracts = CONTRACT_ROUND_TO` ran AFTER the cap, and
  `calc_contracts_for_target` already applies that same minimum — so the repeat
  could only ever raise size past the limit. On a $1,000 account at 5% ($50 budget)
  a $51-max-loss spread sized to **5 contracts = $255 = 25.5% of the account**.
  Extracted as `size_contracts`; a cap that leaves no room now returns no
  selection. ⚠ The existing `test_risk_cap_limits_contracts` asserted
  `result is not None` — it had enshrined the override it was named for.
- **A missing $DECN quote fabricated maximum-bullish breadth.** `(advn/decn) if
  (advn and decn) else (inf if advn else None)` could not distinguish `decn == 0`
  (a real 10/10 tape) from the symbol being ABSENT from the batch, and `inf` maps
  to breadth score **10**. `inf` is now reserved for a genuine zero; either symbol
  missing reads None. (`advn == 0` with decliners stays a real 0.0 — an extreme
  bearish tape is data, not an absence.)
- **The portfolio "execution" grade was graded on hindsight.** `entry_pct` was
  computed over the closes SINCE entry, so a position that rose made its own entry
  the window minimum (grade A) and one that fell made it the maximum (F) — a
  near-monotone function of the subsequent return, which made the 15%-weighted
  execution dimension a re-weighted copy of the 35%-weighted return dimension and
  the stated 35/25/25/15 split behave like ~50/25/25. Now judged against
  `slice_before` — the 60 sessions of range actually on offer at the fill —
  dropping out (and reweighting) when that history is absent.
- **Verification.** options_svc 1200, options-scanner 1172 + the documented 8,
  webgui 2328, sentiment-dashboard 495 + the documented 2, sentiment_svc 325 + the
  documented 1, portfolio-analyzer 194, portfolio_svc 32, trade_svc 77, market_svc
  77, shared 93. Ruff clean tree-wide.
- **Still open** (batch 3): the webgui efficiency debt — the 2 s watcher's ungated
  237 KB reads, the Desk's 11 on-loop seed reads, `/eod`'s on-loop build — plus the
  accumulated first-pass Mediums (pcr duplication, the `tools/` holiday literals,
  the 22-page poll idiom, ~60 formatter clones, the rescue triplication and the
  compute.py split).

**Prior —** 2026-08-20 (**Second-pass audit, batch 1 — a rescue action that
could never be applied, a crash tape that read bullish, and the biggest key in Redis.**
- **Context.** After the day's three audit batches were promoted, the full audit was
  re-run. All ten morning fixes verified independently (no regressions); this entry is
  the remediation of the second pass's High tier + the round-3 NaN cluster.
- **HIGH — `roll_out`/`roll_down_out` could NEVER pass the stale-price guard, and the
  bypass overstated equity.** The two builders emitted only the 2 reopening legs in
  `est_fill_legs` while their `net_cash` included the close debit (−cv) — so
  `paper_adjust._reprice_candidate_net`, which reprices the legs uniformly, read even
  IDENTICAL quotes as drift equal to the whole close cost (measured 187% vs the 15%
  tolerance): every live apply refused with "prices moved — re-review". And with
  repricing unavailable (off-hours), `apply_roll` booked the old spread's close at the
  entry-credit scratch, so the close debit never hit cash — equity overstated by
  `(cv − entry_credit)·100·qty`. **Fix: every roll candidate now carries the CLOSE pair
  ahead of its reopen pair** (`rescue._close_pair`), priced live with a cv-split
  fallback (BUY-back = cv, sell = 0.0) when the old legs are unpriceable — which also
  fixes the second-pass Low where `roll_down`'s 0.0-coalesced close legs booked a
  CREDIT to close a tested spread. The legacy 2-leg shape still applies (cached
  boards), pinned by test. ⚠ The `_leg` docstring claimed est_fill_legs prices were
  "display-only"; they were load-bearing in two places. Corrected.
- **HIGH — `blend_trend` turned a saturated max-bear sub-score into neutral.**
  `scores.get(k, 50.0) or 50.0` — a score of exactly 0.0 is falsy, and 0.0 is the
  ENTIRE clamped crash-tape region of `score_price`. Measured: the most bearish
  possible tape blended **36.5** where one tick off the floor blended **14.0**, a
  22.5-point bullish jump at confidence 1.0, feeding the Day gauge, both structural
  horizons and the classifier's direction axis. Now only absence (or non-finite)
  means neutral; the fix is continuous at the floor by test.
- **HIGH — `cache:options:calc_chain` was 8.77 MB, 53% of ALL prod Redis string
  bytes.** The raw 20-expiry chain, ~40 fields/contract, no TTL, forever. The pages
  read five contract fields; `thin_calc_chain` cuts to that whitelist at publish:
  **8.77 → 0.68 MB (−92%)** measured on the real prod payload, extractors verified
  unchanged. Fields cut, strikes kept — the leg builder legitimately offers far wings.
- **NaN round 3** (`flow_skew._as_float`, `_pick_nearest_delta`, `profile_shape._num`):
  non-finites rejected, and an IV must now be **positive** — Schwab's `-999`
  uncomputable-IV sentinel no longer wins the 25Δ risk-reversal pick. One audit claim
  was REFUTED on reproduction: `detect_uoa` never emitted NaN rows — `int(nan)` raises
  into its broad `except`, an accidental save now pinned by tests so it cannot be
  removed silently.
- **Verification.** options_svc 1200, options-scanner 1379 + the documented 11,
  webgui 2328, sentiment-dashboard 491 + the documented 2, sentiment_svc 325 + the
  documented 1. Ruff clean. All counts grew only by the new tests; failing sets
  compared name-for-name.
- **Second-pass items still open** (not in this batch): the ~5,200 lines of dead code
  stranded in `gamma_tool.py` + friends (with the 11→8 baseline shrink), IC PoP
  `min()` vs `pop_put+pop_call−100`, the width-selector risk-cap override, the missing-
  `$DECN` breadth fabrication, the portfolio execution grade, the 2 s watcher's
  ungated 237 KB reads, and the Desk's on-loop seed.

**Prior —** 2026-08-20 (**The RRG momentum axis — fixed, and my own case for
fixing it was overstated by a factor of ten.**
- **The defect was real at the function level.** `sector_rotation_assessment.
  compute_rs_momentum` subtracted ROC's OWN rolling mean before normalizing, which
  differentiates a second time: it measured the ACCELERATION of relative strength, not
  its rate. Isolated on a controlled RS-Ratio series the sign came out **inverted** — a
  steadily rising ratio read **99.70** ("weakening"), a steadily falling one **100.96**
  ("strengthening"). Dropping the term restores `100 + ROC / rolling_std(ROC)`, the
  standard construction and the one the code's own comment claimed.
- ⚠ **The evidence I first reported was measured on DETERMINISTIC synthetic series, and
  it did not survive contact with real bars.** Deterministic ramps have degenerate
  rolling statistics. Re-measured against two years of live SPY + the eleven sector ETFs
  through the proxy: the old and new formulas agree on **10 of 11 sector quadrants
  today**, agree on the risk-on/risk-off headline for **91% of the last 374 sessions**,
  and **neither** correlates with forward 20-bar excess return (−0.060 vs −0.040). The
  dramatic "steady out-performance is called Weakening" table was an artefact of the
  test series, not a description of the screens. Corrected here because the earlier
  entry and the hand-off both overstated it.
- ⚠ **And the blast radius was narrower than stated.** There are **two** RRG engines.
  `scoring/rotation.compute_rrg_quadrants` (momentum = `RS_today / RS_20-bars-ago`, a
  proper rate) was already correct and feeds `/sentiment/sectors` and
  `/sentiment/momentum` — both **unaffected**. Only `/sentiment/rrg` and
  `/sentiment/rotation`, which read the assessment, move. Worth knowing before touching
  either engine; the table is now in CLAUDE.md.
- **Live consequence to expect:** the corrected spread has a slightly wider tail
  (|spread| p90 1.35 → 1.51), so the ±1.5 `RISK_THRESHOLD` fires on ~10% of sessions
  where it used to fire on ~6%. Verified live after the fix: defensives (XLP/XLU/XLV/
  XLRE) all Leading, XLK Lagging, headline −1.69 → risk-off — a coherent defensive
  rotation.
- **Method note worth keeping:** the useful test here was not a bigger synthetic, it was
  fetching real bars through the proxy and asking whether the two formulas actually
  disagree, and whether either predicts anything. Both answers were "barely". A
  correctness fix can be right and still not matter much, and saying so is part of the
  report.
- **Verification.** sentiment-dashboard 485 + the documented 2, sentiment_svc 325 + the
  documented 1, webgui 2320. Ruff clean. `test_rs_momentum_semantics.py` was rewritten
  from documenting the defect to pinning the corrected semantics, and carries the
  real-data scale note so nobody reads the sign inversion as a claim about the screens.

**Prior —** 2026-08-20 (**Audit batch 3 — 20,105 lines deleted, and the
`shared/analysis_lib` init that was breaking its own package.**
- **The headline: `shared/analysis_lib` was an abandoned Tk APPLICATION wearing a
  library's name.** ~9,600 of its 11,406 lines — the "Blueprint Analyzer" GUI, its
  agents, a `schwab_client` documented in-repo as **broken** — had no callers outside
  each other, and `__init__.py` eagerly imported all of it. ⚠ **That eager init is
  precisely WHY all four live consumers carry a `sys.path` bootstrap**: a plain
  `from shared.analysis_lib import technical` *raised*, so every consumer imported the
  module standalone to dodge the package. Verified by watching the new surface test
  fail with a real `ImportError` from `schwab_client.py:27` before the deletion.
  `technical`/`sector_analysis` now resolve `config` relatively under the package and
  by bare name standalone, branched on `__package__` rather than `try/except` — so a
  genuine error in `config.py` cannot fall through and silently bind ANOTHER app's
  `config`, which is the exact cross-app collision the repo already documents.
- **The headless service was importing a GUI toolkit.** `options_simulator/__init__.py`
  eagerly imported its Tk window, so the first `sim_fetch` in `options_svc` pulled
  **106 tkinter/matplotlib modules** in through the package init — defeating the
  deliberately-lazy import at the call site. Measured in a subprocess before and after;
  both guards (`test_simulator_headless.py`, `test_analysis_lib_surface.py`) probe a
  FRESH interpreter, since an in-process `sys.modules` check is poisoned by test order.
- **Deleted, with their tests and launchers:** the Blueprint Analyzer (11 modules +
  `agents/`), `gamma_window_legacy.py`, `options_simulator/window.py`, and the legacy
  CLI the user signed off — `eod_report.py`, `scanner.py`, both `notifier.py`s,
  `backtest_0dte.py` — plus `ai_prompt_builder.py`, `trade_analyzer.py` (the dropped
  Dash popup's engine), `execution.py` and `headless_snapshot.py`. **37 files, 20,105
  lines**, against an audit estimate of ~13,500.
- **KEPT, deliberately:** `validate_market_state.py`, `validate_new_symbols.py` and
  `gex_direction_log.py`. They look like the same legacy cluster but they are research
  harnesses that own a data artifact and answered a real question — `validate_market_state`
  produced the five-state validation study, and `repo_paths` still exports its output
  paths. The line drawn was "superseded product code" vs "a tool that answers a question".
- **Two follow-on cleanups the deletion unlocked:** the four `shared/analysis_lib`
  carve-outs in `pyproject.toml`'s ruff exclusion list existed only for the Tk modules,
  so the package is linted like the rest of the stack again; and the dev/prod runbook's
  known-limit about the two legacy notifiers sitting outside the notification gate is
  now moot — they are gone.
- ⚠ **One audit claim did NOT hold.** It said deleting `gamma_window_legacy.py` would
  also remove the documented flaky Tk-root skip race. It does not: the three racing
  tests (`test_chart_style_vars`, `test_gex_dex`, `test_theme`) import `gamma_tool` and
  `theme`, both of which are live. The race is unchanged — options-scanner still reports
  2-3 skipped at random.
- **Medium accuracy items, same batch.** `AGG_WEIGHTS` sums to **1.30**, and the
  "aggregate confidence" it returned was the raw weighted sum, so a fully-confident read
  published **1.3** into `market_state_history_db` and every downstream consumer that
  treats it as [0,1]. Now divided by the total weight; the SCORE is bit-identical (it
  already divided by the same sum), and the existing `test_single_component_present` had
  been pinning the broken value. And `market_svc.classify._num` coerced a missing
  `lastPrice` to **0.0**, so a partially-populated quote rendered a real tile reading
  "0.00" coloured flat — `normalize_quote` now returns None and falls into the `no_data`
  path that already existed, needing no new branch downstream.
- ⚠ **The RRG momentum axis is a REAL defect and was deliberately NOT fixed.** Measured:
  a sector **steadily out-performing** the benchmark is classified **"Weakening"**, and
  one **steadily under-performing** is classified **"Improving"** — only *acceleration*
  reaches "Leading", because `compute_rs_momentum` subtracts ROC's own rolling mean.
  Correcting it re-assigns every quadrant on `/sentiment/rrg`, `/sentiment/rotation` and
  `/sentiment/momentum` and moves the cyclical−defensive risk-on/off headline with them
  — a product decision, not a bug fix. The false comment claiming "RS-Momentum > 100
  means strengthening" is corrected, and `test_rs_momentum_semantics.py` DOCUMENTS the
  present behaviour under names that say so. Batch 1 was a lesson in characterization
  tests being mistaken for specifications; that test file says outright it is not one.
- **Verification.** webgui 2320, options_svc 1189, options-scanner 1371 + the documented
  11, sentiment-dashboard 479 + the documented 2, sentiment_svc 325 + the documented 1,
  driver_svc 239, market_svc 77, trade_svc 77, portfolio-analyzer 189, portfolio_svc 32,
  schwab-proxy 98, shared 93/25/49, tests 69, tools 816. **Ruff clean across the whole
  tree**, now including `shared/analysis_lib`.

**Prior —** 2026-08-20 (**Audit batch 2 — three unbounded payloads, and a
comment that had been lying about the biggest one for two months.**
- **`cache:options:gamma` was 4.99 MB, not the "well under ~1 MB" its own comment
  claimed.** The 2026-06 crop bounds the STRIKE axis; the TIME axis keeps growing all
  session, so each view's `history` reaches ~1.1 MB by the close — **4.59 MB of history
  against ~400 KB of everything else** — and the page draws ONE view at a time. Each
  view's history moved to its own key (`gamma_history_key` / `_publish_gamma`), fetched
  on demand by the page and cached per gamma version. **Main payload −92% (4.99 → 0.40
  MB); page read per version bump −67% (4.99 → 1.65 MB).**
- ⚠ **The write side does not improve during collection** and it would have been easy to
  claim it did: all four histories move every minute, so publishing costs the same bytes
  plus key overhead. The win appears once collection stops, where frozen histories
  `skip_unchanged` and a refresh costs 0.40 MB instead of 4.99 MB. Measured both
  directions rather than halving one and doubling it.
- **Write ORDER is load-bearing**: history keys first, main payload second, because the
  page reacts to the main key's version and then reads history — so history-already-
  written is the only skew it can observe. Each history payload carries its **symbol**
  and the reader refuses a mismatch (a stale history within one symbol is benign, since
  the rows are append-only for the session; across symbols it would draw one symbol's
  heatmap under another's bars). A view the snapshot lacks is published EMPTY, never
  skipped, so the previous symbol's rows cannot linger.
- **`driver_account_view` published every closed trade ever** — 160 rows / 158 KB of a
  224 KB payload, growing ~1 KB per trade forever, while `orders` beside it had carried a
  `limit=100` all along. ⚠ **The obvious fix was wrong:** that list feeds a **lifetime**
  count / win-rate / realized total, so a bare "keep the last N" would have silently
  misreported the driver's whole track record. The rows are capped at
  `DRIVER_CLOSED_LIMIT`; `closed_totals` is computed over **every** closed row and
  carries `truncated`, which the summary line now discloses ("showing the most recent
  N") per the house no-silent-caps rule.
- **`publish_bullbear` did three full JSON round-trips every ~30 s** for data that
  changes once a night: the 304 KB momentum payload (134 KB of it `rank_history`, which
  the builder never touches) plus its own 190 KB output, re-read for a timestamp
  compare. Both are version-gated memos now (~2,880 ticks/day → one deserialize each
  per change). ⚠ It cannot reach zero — `cache_set(skip_unchanged=True)` must read the
  stored payload itself to decide whether to skip — so the test asserts the achievable
  one read, not the tidy zero it was first written to expect.
- **Verification.** webgui 2320, options_svc 1189, sentiment_svc 325 + the 1 documented,
  driver_svc 239. Ruff clean. Payload figures measured against the live prod Redis (db 0),
  read-only.
- **Still open** (batch 3): ~13,500 lines of provably dead code — `shared/analysis_lib`
  is ~85% dead and its eager `__init__` is *why* four live call sites do sys.path
  gymnastics; the Tk remnants drag **tkinter + matplotlib into headless options_svc**.
  Plus the medium-tier accuracy items (`AGG_WEIGHTS` summing to 1.30,
  `market_svc.classify._num`'s silent 0.0, the RRG "momentum" axis that actually
  measures acceleration).

**Prior —** 2026-08-20 (**Accuracy audit, batch 1 — five wrong numbers on live
screens, and in three of the five a TEST was holding the bug in place.**
- **What this was.** A four-agent audit across calculation accuracy, compactness and
  efficiency. This entry covers the batch-1 remediation: everything ranked Critical or
  High for *accuracy*, plus one correctness item the compactness agent surfaced. The
  efficiency pass found **no** High-tier findings — the 2026-07-18/19 remediation held,
  and the post-08-01 Desk / Bull-Bear / sector work is structurally clean.
- **CRITICAL — `calculate_adx` misattributed directional movement.** `-DM` was built from
  `|delta_low|` instead of the signed `-delta_low`, the direction gate `minus_dm < 0` was
  tautological, and the `-DM` branch compared against an already-filtered `plus_dm`.
  Measured **76.58 where the textbook value is 32.5**, and a **dead-flat tape read ADX
  100** — maximum trend strength — which is exactly the bias the regime classifier was
  showing toward Trending. Reach: the live Day trend needle, the Week/Month structural
  arcs, `regime_evidence`'s `adx`/`adx_rising` tells, and the Trade page. `trade_svc`'s
  own `_adx_series` had the formula right the whole time. Details + the two lessons in
  [CLAUDE.md](../CLAUDE.md).
- **HIGH — put charm carried a spurious `+ r*exp(-r*T)`.** With q=0 put charm *is* call
  charm (put delta differs by a constant), so every put strike was biased by **+0.0449**
  — ~+$575M of phantom charm on one 20k-OI SPX wing strike, and ~$8-10B on the net-charm
  total. `bs_charm` had never been pinned by a single test; it now has an identity test
  and a finite-difference test on both sides.
- **HIGH — a naive datetime meant CENTRAL, and three time helpers read it as EASTERN.**
  Replay and IV-shock priced every bar with **T + 1 hour** (0-DTE ATM $5.80 vs a true
  $4.10, and live premium shown 30 minutes after expiry); the Calculator's P&L grid
  hand-rolled the same mistake, so its "Now" column disagreed with its own summary tiles
  and the "Exp" column printed ~$4/share instead of the kinked payoff; and the What-if
  sweep used whole-day DTE floored at 0.01 days, pricing every 0-DTE leg — and its P/L
  baseline — at T ~ 14 minutes regardless of hours left (**4.6x** understatement at five
  hours to the close). ⚠ **The one-hour error was introduced by audit item C6**, which
  moved the branch from `hour=15` to `hour=16` and wrote tests asserting a naive clock
  was Eastern; the pre-C6 value had been correct for CT input all along. The naive branch
  now localizes to `NAIVE_WALLCLOCK_TZ` and the two callers delegate to the shared
  helpers (`expiry_time_to_years`, the new `compute._leg_days_to_expiry`).
- **HIGH — the VIX Complex blend did not renormalize.** An undefined sub-component added
  0 to the numerator with no change to the denominator, dragging the score toward 1
  whenever `$VIX1D` was missing (routine off-hours): term 6 + slope 8 published **4**
  where the present components average **6.5**. VIX Complex is 20% of the composite. The
  *confidence* deliberately still falls to 0.67 — that is how the top-level composite
  down-weights a thinner reading.
- **HIGH — four more scorers still had the NaN-clamps-to-the-HIGH-bound trap.** Measured:
  `score_vix_context(nan, ...)` returned **70.0 at confidence 1.0** — a confidently
  bullish trend read from no data — and `score_breadth_dir(nan, ...)` returned **100.0**,
  maximum bullish breadth. Separately, `effort`/`rejection_defense`/`session_structure`
  each had a `_num` that caught `None` but passed NaN straight through, so one NaN volume
  pinned effort's `updown_vol` **0.0039 -> 1.0** at unchanged confidence. Two different
  layers, two different fixes — neither is the banned `_clamp` shortcut.
- **Plus one correctness item from the compactness pass:** `sentiment_svc.handlers._is_rth_now`
  tested weekday + clock with **no holiday check**, while its docstring claimed to mirror
  `scheduler._is_rth`, which has one. Intraday recording and regime publishing ran through
  every NYSE holiday. It now delegates to the one gate.
- **The meta-finding, and the reason three of these survived so long: a test was holding
  the bug.** `test_adx_uses_wilder_smoothing` pinned `47.0052` — the buggy output — because
  a characterization test records what the code *does*; `test_replay_T_uses_1600_not_1500`
  pinned the one-hour overstatement; and the 2026-07-01 audit had closed a finding
  asserting the +DM/-DM rule "was already correct". The replacements are **property-based
  or cross-implementation**: a flat tape is not a trend, mirroring a series cannot change
  trend *strength*, a naive CT wall-clock must agree with its own tz-aware instant, and
  put charm must equal call charm. Those are claims about the world, and no amount of
  re-running the wrong code makes them pass.
- **Verification.** All suites at or above their documented baselines with **no new
  failures**: webgui 2309, options_svc 1181, options-scanner 1486 (the documented 11-fail
  set, node-for-node), sentiment_svc 321 + the 1 documented, sentiment-dashboard 496 + the
  2 documented Tk, trade_svc 77, driver_svc 239, market_svc 73, portfolio_svc 32,
  shared 89/25/49, tests 69. Ruff clean. One test fixture moved deliberately:
  `_range_bars`' seed 59 -> 8, because with the corrected ADX seed 59 sat at the 93rd
  percentile of its own generator's distribution (the generator is unchanged).
- **Still open** (batch 2/3, not started): the `cache:options:gamma` payload regrown to
  **4.65 MB** with a comment still claiming "<1 MB", an unbounded `closed_positions` list
  in the driver account view, a 30-second full-deserialize of the 304 KB momentum payload,
  ~13,500 lines of provably dead code (`shared/analysis_lib` is ~85% dead; the Tk remnants
  drag **tkinter + matplotlib into headless options_svc**), and the medium-tier accuracy
  items (`AGG_WEIGHTS` summing to 1.30, `market_svc.classify._num`'s silent 0.0, the RRG
  "momentum" axis that actually measures acceleration).

**Prior —** 2026-08-20 (**The Bull / Bear Map — "bullish" is two facts, and this
screen refuses to blend them, refuses to add a third regime verdict, and had four of its
upstream assumptions turn out wrong on contact with the producer.**
- **What shipped.** `/sentiment/bullbear`, a new **third tab** of the Trend & Sentiment
  group (`SENTIMENT_CHILDREN` is now seven; the rail is unchanged at 14 items, since
  this is a tab and not a rail page), plus an **eleven-chip sector strip on the Desk**
  that clicks through to it. `sentiment_svc` publishes ONE new view,
  `cache:sentiment:bullbear`, merging the nightly momentum cascade with one batched
  quote call; Tier 1 renders a lazily-expanding sector → industry → stock tree. **No
  Highcharts** — it is a tree, and that also dodges the documented mount-hidden collapse
  trap. Detail in [webgui-routes.md](webgui-routes.md#sentimentbullbear); design + plan
  in [`plans/2026-08-19-bull-bear-map-design.md`](plans/2026-08-19-bull-bear-map-design.md)
  / [`-plan.md`](plans/2026-08-19-bull-bear-map-plan.md).
- **The organising idea: two axes, never blended.** Every row shows absolute trend
  (`raw.trend`, the annualised exp-regression slope of log(close) scaled by R² — signed,
  benchmark-free) and relative strength vs SPY (`raw.excess`) as **separate marks**, plus
  a live day-move. Their four combinations are the map, and the fourth is the reason the
  page exists: **Falling · Leading — down, but down less than the index — is precisely
  the row a relative-strength-only screen paints as a buy.** Measured on the 2026-08-19
  payload, 19 stocks and 1 industry sat in that bucket. The quadrant chip names BOTH
  axes, because one word is the ambiguity being removed; ties go the cautious way (a flat
  trend is not "rising", a zero excess is not "leading"); and a missing axis renders
  **No reading**, an absence rather than a neutral verdict.
- **Participation is a third, independent dimension, drawn beside the quadrant and never
  folded into it.** It is the share of a group's constituents confirming the move, and it
  separates two rows that look identical on trend alone — 2026-08-19: Energy flat on
  **0.96** participating, Real Estate rising on **0.23**. At or below a third the move is
  flagged thin and the bar switches to the down hue. Stock rows carry no bar at all,
  which draws differently from an empty one: no track is "no constituents", an empty
  track is "nothing confirms".
- **Deliberately NO regime headline — the page's central design decision.** CLAUDE.md
  already records `/sentiment/sectors` and `/sentiment/rotation` printing OPPOSITE
  risk-on/risk-off verdicts from non-commensurable quantities (measured 2026-08-17:
  `+0.37` rendering "Risk-on" beside `−1.52` rendering "Risk-off"). This page does not add
  a third. Its headline is **quadrant counts** — "5 of 11 sectors rising and leading" —
  arithmetic about the rows on screen, not interpretation, and `payload["regime"]` is
  never read by either the page or the Desk strip. `bullbear.headline` returns **`""`** on
  an empty payload rather than "0 of 0 sectors rising and leading", which would state a
  maximally bearish tape nobody measured — so the cold-cache state is a real message
  naming the nightly cascade, not a fabricated count. The count strip keeps all four
  quadrants **even at zero**, because an empty trap bucket is itself a reading.
- **Two clocks, deliberately, and they fail separately as well as tick separately.**
  `computed_at`/`session_date` date the SCORES (last night's cascade — trend and relative
  strength need months of history, so there is no intraday version of them); `quoted_at`
  dates the day-moves. `quoted_at` is `None` when the live quote call raised, and
  `compute.bullbear_view` ships the tree anyway: the cost is one column, not the page. A
  malformed tree by contrast RAISES, since an all-None day-move column would hide it.
  Page-side the same distinction is two repaint paths — a version carrying only new quotes
  **reprices the day cells in place**, because rebuilding would collapse every branch the
  reader had opened, twice a minute.
- **One batched `/quotes` call covers all 374 distinct symbols** (11 sectors + 69
  industries + 296 stocks, deduped because an industry ETF is usually a scored stock
  too) — verified against the running proxy that 374 come back in a single call, so this
  is one request per poll and not one per name. The publish is gated on **whether the
  tape is open, not on RTH** (`scheduler.bullbear_due`): every ~30 s tick in any open
  session including GTH and curb, throttled to once per 5 min on a genuinely closed tape.
  Sharing `refresh_due`'s 15-minute off-hours gate would have been a false economy — that
  knob was sized for the 120 s composite refresh at 30–40 proxy calls a run, ~35× this
  one's cost, and it would have left the Today column fifteen minutes stale through
  exactly the extended sessions a reader most wants current.
- **The tree expands lazily**, so 376 rows are never all in the DOM: the default screen is
  eleven sector rows, industries build on a sector expand and stocks on an industry
  expand. Every branch adds at least one child — a note where there is nothing else — so
  the "already built" check can never misread. Both empty states are real, not
  hypothetical: 3 of 69 industries held no admitted member stock on 2026-08-19, and 10 of
  296 stocks resolved to no scored industry at all.
- **⚠ Correction to commit `a2f596d`'s message, which overstates what it did.** Its body
  says the commit is "the tests that pin it, plus **the two defects they turned up**" — a
  leaf row offered a chevron it could not honour, and `_rebuild` keeping the previous
  tree's day-cell registry. **That is false, and the record needs to be straight: the
  commit changed exactly one file, `webgui/tests/test_sentiment_bullbear.py`, +156 lines
  and zero deletions.** Both items were already correct in the page as shipped by the
  preceding commit `3fc8c67` — `_mark_row(node, level, leaf)` already suppressed the
  chevron for a leaf, and `_rebuild` already opened with `state["cells"] = []`. They were
  **coverage gaps closed by new tests, not defects found**. This matters because CLAUDE.md
  already carries a scar from exactly this class of overstatement: the 2026-07-01 accuracy
  audit closed finding C7 ("single-source `r`") as FIXED while five `0.045` literals sat
  in `gamma_tool` and a separate `RISK_FREE = 0.04` in `backtest_0dte` for another seven
  weeks. **A future session must not read that commit and infer this page shipped broken.**
- **The session's through-line: four upstream assumptions in the plan were wrong on
  contact with the producer, and every one had been reasoned plausibly from the consumer
  side.** (1) **The quote shape** — `schwab_client.get_quotes` returns a **FLATTENED**
  `{symbol: {"change_pct": …}}` mapping (`schwab-proxy/proxy_client.py`), not the raw
  Schwab `{"quote": {…}}` envelope the plan's fixture invented; shipped, all 374 rows
  would have carried `day_pct=None` silently **while the plan's own test passed**, because
  the test asserted against the same invented shape. (2) **`participation` names two
  different quantities in one row** — `row["participation"]` is the 0..1 share this page
  wants, `row["components"]["participation"]` is a within-level **z-score**, signed and
  unbounded (both set by `compute._momentum_score_level`); the wrong one costs every
  negative row its bar and mis-draws the rest, with no exception and no blank render.
  (3) **The orphan mechanism** — the plan attributed `orphan_stocks` to the four
  duplicate-ETF industries, which cannot produce one: `_momentum_universe` puts those in
  `orphans` rather than `universe["industries"]`, and `industry_of` is built only from the
  latter, so such a stock resolves to `("", "")` and is dropped from every row and count.
  The real source is the admission gate. That wrong version had already been written into
  a test docstring on the plan's authority before reading the producer caught it.
  (4) **A `ZoneInfo` the module does not import** — the plan's `quoted_at` stamp assumed a
  CT constant that belongs to `options_svc`, not to `sentiment_svc/compute`; the stamp now
  mirrors how the momentum payload produces its sibling `computed_at`, since the two
  render side by side and would otherwise format differently. **The lesson to carry: a
  green test over a fixture you wrote yourself is a test of your assumption, not of the
  producer.** Where a doc or a test claims something about upstream behaviour, name the
  module it was read in.
- **Docs.** New `/sentiment/bullbear` section in
  [webgui-routes.md](webgui-routes.md#sentimentbullbear); the route-table row in CLAUDE.md;
  `webgui/page_help.py`; `## Bull / Bear Map` in both the User Guide and the Reference
  Guide, with the manuals rebuilt to HTML + `.docx`. Two durable invariants were also
  corrected in CLAUDE.md in place: the flattened-`get_quotes` trap above is now recorded
  under the proxy environment quirks, and `[rotation]`'s scope note — which read
  "`/sentiment/rotation` only" and had **already** been stale since the 2026-08-17
  rebuilds put RRG and Momentum on the same tokens — now names all four screens that
  share it. The 30-second cadence line in both manuals gained the closed-tape throttle it
  was missing.)

---

**Prior — 2026-08-19** (**The Options Strategy Calculator rebuilt to a three-step
screen — and three readouts it could always have derived but never did.**
- **What shipped.** `/options/calculator` rebuilt from a supplied design: ① STRATEGY and
  ③ LEGS fill a fixed 424 px input column with the action grid under them, while
  ② SYMBOL, six metric cards and the P&L matrix fill the results column beside it. The
  design arrived as a `.dc.html` component shipping its own Black-Scholes and a mock
  chain, because it had to run standalone; **none of that is ported.** The page stays a
  Tier-1 reader — same three commands (`calc_load` / `calc_compute` / `calc_iv`), same
  three cache views, **no Tier-2 change at all**. Detail in
  [webgui-routes.md](webgui-routes.md#optionscalculator); design + plan in
  [`plans/2026-08-19-calculator-redesign-design.md`](plans/2026-08-19-calculator-redesign-design.md)
  / [`-plan.md`](plans/2026-08-19-calculator-redesign-plan.md).
- **A sixth page-scoped theme language, `[calc]`.** Near-black ground, cyan/green/amber
  signals, JetBrains Mono — deliberately unlike the app-wide navy the Simulator and
  Trade wear. Scope hook is **`.calc-v3`**, never `.calc-v2`, and a test pins that:
  restyling `.calc-v2` from here would silently reskin two other pages. Same shape as
  `[console]` / `[macro]` / `[sectors]` / `[rotation]` — TOML knobs, Tailwind
  class-string tokens out of a builder, degrade-to-defaults so it can never break
  startup. The heatmap's green/red cell ramp stays OUT of the config, being a
  data-driven map — the category CLAUDE.md already excludes.
- **The theme shipped decorative, and a review caught it.** Six `.strat-menu-calc` rules
  had no element carrying that class (Quasar teleports the menu popup to `<body>`, so
  the hook has to be applied at construction), and `boxed=True` paints the *navy*
  `STRATEGY_BTN` onto the trigger, which `build_calc_css` never contests — so the
  picker and its popup would both have rendered app-navy on a near-black page.
  `strategy_menu` gained `menu_class` / `btn_class` (and later `caption`), each
  defaulting to exactly the previous behaviour, so the Simulator and Rescue are
  byte-identical. **A page-scoped CSS rule whose class nothing carries is dead, and no
  test of the CSS *string* can see that.** In the same pass `CALC_STATE_TEXT` was found
  to hold token NAMES while its only possible caller is `Element.classes(remove=…)`,
  which takes classes — as shipped it could not do its job.
- **The matrix `%` column changed meaning, and now names its own basis.** It was a share
  of *premium received*. It is now **`% MAX`** — of the summary's `max_profit` — when
  that is a real capped return; **`% COST`**, of the debit paid, when it is not but the
  position was bought; and a bare **`%`** over em-dashes when neither exists. The
  heading is part of the basis rather than a separate decision, because *a percentage
  whose denominator the reader has to infer is worse than no percentage at all*. For a
  credit structure `% MAX` is numerically identical to the old column (`max_profit` IS
  the entry credit), so those screens did not move. ⚠ on the generic numeric path
  `max_profit` is `max(pnl)` over the service's own grid, not a closed-form cap — widen
  the grid and the denominator can move; the analytic paths do not drift.
- **Three new readouts, all derived page-side from payloads already in the cache.**
  Per-leg **delta**, read from the chain's own `delta` field — the one `flow_alerts`
  reads, so it is market delta rather than a second pricing model living in Tier 1;
  a **NET / MAX LOSS strip** on the ③ LEGS frame; and the chain **status pill**.
- **Every one of them had to be taught to say nothing.** Delta renders an em-dash, never
  `0.00`, for anything outside `[-1, 1]` — Schwab's `-999.0` missing-greek sentinel —
  and index chains read hollow outside regular hours, so that is the *ordinary* case.
  `net_premium` is `None` while any leg is unpriced, which is every fresh template
  before Fetch Premiums; `NET $0` over an unpriced structure would state a figure the
  page does not have. `max_loss_estimate` is `None` when the loss is unbounded or when a
  short leg outlives a long one, which its single-date model cannot see. And the six
  cards render **`Unlimited`** where the service returns its `999999` sentinel, never
  `$999,999`. Same family as the `_clamp(nan) → hi` trap already documented here: a
  missing input that renders as a confident number.
- **The final review found four more places the screen stated a figure it could not
  stand behind, and all four are now em-dashes or `Unlimited`.** (1) RETURN ON RISK
  covered only two of the engine's three zeroing conditions, so a `max_loss` of `0.0`
  printed `0.0%` / `0.00% per day`; MAX RISK printed `$0` in red beside it while the
  ③ LEGS strip read $29,965 for the same legs — out of the numeric path a zero max
  loss means the grid never reached the loss. (2) The per-leg DELTA fell back to a
  cross-expiry match when its own contract had none, and since `extract_delta` cannot
  tell "absent" from "sentinel", a December leg rendered August's delta. (3) The ENTRY
  card's position note took the LARGEST leg qty, so a 1-2-1 butterfly read "2 contracts"
  beside a CONTRACTS field showing 1 — and it is the only place position size appears,
  so nothing corroborated it. (4) The generic path's `max_profit` is `max(pnl)` at the
  grid edge, which for a net-long-call structure is not a cap at all: the same long call
  read `Unlimited` / `% COST` untouched and `$32,780 at 30d expiry` / `% MAX` — with
  `RETURN ON RISK 7804.8%` — once any strike was nudged and the routing flipped to
  `CUSTOM`. The legs now decide it (`profit_uncapped_above`, the mirror of the
  net-short-call test `max_loss_estimate` already ran), so both routings land on one
  screen. **Four instances of one rule**, and the fourth was inherited from `main`
  rather than introduced — the new self-naming `% MAX` heading is what turned a quietly
  odd number into an explicit claim.
- **Five tests in this series could not fail, and the fifth was caught by mutating it.**
  Both tests pinning the position note used uniform leg quantities, so `max` → `min`
  left the suite green. In the same pass the new integration file's timer collector —
  which `pytest.skip`ped unless it found exactly three timers — was changed to select
  the three polls **by name**: adding any unrelated render-time timer turned all ten of
  those tests into silent skips, which is the options-scanner failure mode recorded
  further down this file. Verified by adding a fourth timer: ten skips before, ten
  passes after.
- **Max loss is exact, not a width heuristic.** The expiration payoff is piecewise
  linear with corners only at the strikes, so evaluating net premium plus intrinsic over
  `{0} ∪ strikes` finds the true minimum — no per-structure special cases. An iron
  condor correctly risks ONE side, a 1-2-1 butterfly's qty-2 middle leg needs no
  handling of its own, and a lone short put reads its real `strike × 100 − credit`
  instead of a width that does not exist.
- **The shared leg editor gained a card layout, and the Simulator took it too.** The
  GEOMETRY is shared; the palette enters as `tokens`, so the Calculator paints it
  `[calc]` near-black while the Simulator stays app-navy and `leg_editor` imports no
  page's theme constants. An omitted cell **collapses its track** rather than leaving a
  hole — a 4-track grid fed 3 cells slides the next value under the wrong caption — so
  the Simulator, whose `sim_meta` carries no greeks, gets no DELTA column at all rather
  than a captioned column that can never hold a value. Rescue keeps the row table.
  Along the way the Simulator's leg removal acquired a **1-leg floor** where row mode
  allowed zero: a zero-leg simulator enqueues nothing and silently freezes its charts on
  the previous sweep, so a locked ✕ with a tooltip beats an inert page.
- **The mock's two-leg floor was an artifact, and copying it removed real
  functionality.** The design locks removal at two because its own `buildLegs` pads a
  single-leg spec with a synthetic opposite leg. This app does not pad — it ships four
  genuine single-leg templates, and this same redesign wrote tags and a thesis for all
  four — so the floor is ONE. **The top-level Expiry was kept for the mirror-image
  reason:** the mock has per-leg expiry only, but the real page's top-level Expiry drives
  `calc_compute`'s `expiry` argument and the `apply_expiry` propagation to every leg, so
  dropping it would have deleted working behaviour to match a mockup that never had to
  call a service. It moved into the ② SYMBOL readout row instead.
- **A result belongs to the symbol it was computed for.** Loading a *different* symbol
  now drops the on-screen result so the panels fall back to the placeholder; reloading
  the *same* one keeps it, which is what the restore-on-navigation path does on every
  return visit, so an always-clear would blank the screen there. The bug predates this
  redesign — the new status pill is what made it self-contradicting, leaving one
  symbol's cards and matrix under a pill announcing another's.
- **Two module-level stashes were leaking between tests.** `_LAST_CALC` and the handoff
  stash both survive a render by design, so one test typing a symbol had the next
  render restore it — a failure that moves around under random ordering. The fixture
  clears both.
- **Docs.** `page_help.py`'s Calculator entry described the old layout, per the standing
  rule that the hover guides rot first; the User Guide additionally still listed **Range
  min / max and Range %**, controls replaced by the Number-of-strikes input some time
  before this redesign, and both manuals described the leg editor as a table of rows.
  All corrected, along with the route doc and CLAUDE.md's `[calc]` entry.)

---

**Prior — 2026-08-19** (**Market Dashboard: breadth off the equity tape, a
ranked broad-market frame, and a guaranteed newest-first Captured table.**
- **The advance/decline meter counted the whole board, which made it close to
  meaningless.** By the board's risk polarity a bid VIX, a stronger dollar and a
  rallying Treasury are all *declines* — so on a genuine risk-off session the macro
  hedges cancelled the equity selling out and the meter sat near even on exactly the
  days it should read hardest. Measured against the live payload the day it shipped:
  **19 adv / 43 dec** whole-board versus **12 / 20** across the equity frames, i.e. 23
  of the 43 declines came from instruments that are not stocks. `BREADTH_CATEGORIES`
  is now Broad-Market ETF · Top 10 · Sector SPDR · Thematic / Industry ETF (35 tiles),
  pinned by a test against `symbols.CATEGORY_ORDER` so a typo cannot silently count
  nothing. The BIG10 basket is skipped — it is the average of the ten constituents
  beside it in the same frame, so counting it double-counts the mega-caps.
- **Broad-Market ETF became the fifth leaderboard frame.** Its curated
  SPY/DIA/QQQ/IWM-then-equal-weights order was chosen to read as a fixed layout, and
  CLAUDE.md, the route doc and a test all recorded that as deliberate. But the six are
  peers of each other — large vs mega vs small cap, cap- vs equal-weighted — so which
  leads on the day is the single most useful thing the frame can say, and a fixed
  order hides it. One tuple entry; `compute.rank_tiles` already did the work.
- **Captured Signals is now sorted newest-first rather than incidentally so.** The
  service happened to return the 32 open signals in that order, so this changes
  nothing visible today and everything about whether it stays true. The sort **parses**
  `first_seen_ts` instead of comparing the strings: the stored UTC offset shifts with
  DST (`-05:00` summer, `-06:00` winter), so two identical wall-clock texts can be an
  hour apart as instants — and the displayed `Opened` column is truncated to the
  minute besides, so it cannot be the sort key. Undated signals trail in service order
  via a stable sort, so a pair of them never jitters between the 2 s repaints.
- **Docs.** `page_help.py`'s Captured entry still described the **Entry / Current /
  Drift score columns**, which `captured_columns` dropped some time ago — corrected in
  the same pass, per the standing rule that the hover guides rot first.
- **A day footer under the Captured Signals table**, added the same day: opened today ·
  closed today · P&L today (booked) · P&L today (open). The first three ride in a new
  **`day`** block on `cache:options:captured` (`compute.captured_day_summary`, over the
  new `signal_db.count_opened_on` and the existing `get_outcomes_for_date`), dated in
  **CT** because that is the timezone `first_seen_date` and `close_date` are written in.
  "Opened" counts CAPTURES, so a signal taken and closed in one session lands in both.
- **The open P&L is summed page-side**, off the same `signals` list the table renders,
  so the footer can never disagree with the P&L column above it — and it reads an **em
  dash rather than `$0.00` while nothing is priced.** Verified live and worth stating
  plainly: the persisted view carries no marks at all (`signal_marks` has not been
  written since **2026-06-17**), so all 289 open signals summed to a confident `$0.00`
  in the first cut — a flat book reported where there is no reading. Same failure mode
  as the NaN-clamps-to-the-bound trap in `sentiment_svc`, arrived at from the opposite
  direction. An empty book still shows a true `$0.00`; a partly-priced one names its
  coverage on hover.
- **The trap this feature had to survive: three writers, two of which rebuild.**
  `cache:options:captured` is written by `refresh_captured`, by the `captured_reprice`
  command and by `remove_closed_from_captured` — and the latter two construct
  `{"signals": …}` from scratch, so they dropped the `day` block and blanked the
  footer until the next full refresh. Visible only by opening the page *after* a
  reprice or a close, which is exactly the kind of gap a behavioural test does not
  cover. All three now go through **`handlers._publish_captured`**, which re-reads the
  summary on every publish (a close changes the day's closed count as it happens), and
  a source-level test pins the single remaining `cache_set(CACHE_CAPTURED,` call site.)

**Prior — 2026-08-19** (**One risk-free rate, and a CVE baseline of zero — both found
by auditing the unmerged branches rather than the code.**
- **Why this happened at all.** Auditing the three unmerged branches (see the previous
  entry) turned up a July `config-consolidation` branch whose calendar phase had shipped
  but whose smaller items never did. Two were worth taking.
- **The rate.** `options_calculator.RISK_FREE_RATE = 0.045` was already canonical, and
  the calculator, simulator and `compute.calc_iv` already imported it. Three places did
  not: `gamma_tool` carried **five** `0.045` literals, `options_svc.compute`'s projection
  band a sixth, and `backtest_0dte` its own **`RISK_FREE = 0.04`**. Only the backtest
  actually diverged. Measured on an SPX-like put (6800/6750, 15% vol): **0.23%** of the
  option price at 0DTE, 0.33% at 1 DTE, 0.69% at 7 DTE — so never a pricing problem, but
  a comparability one, since the backtest exists to be read against the live scanner's
  credits and was quietly pricing on a different curve.
- **⚠ Two artefacts both asserted this was already done.** The 2026-07-01 accuracy audit
  closed finding **C7 ("single-source `r`")** as **FIXED**, and
  `test_expiry_time_rate_consistency.py`'s docstring claimed "a single `RISK_FREE_RATE`
  source of truth" — while the exact 0.045-vs-0.04 divergence C7 names survived in
  `backtest_0dte` for seven more weeks. The test only checked the three modules that had
  been converted, so **the guard was as narrow as the claim was broad**. It now also
  covers `gamma_tool` and the backtest, reads `project_exposure_forward`'s DEFAULT off
  the signature (a default binds at import, invisible to a module-level value check), and
  adds a **source-level** guard that fails on a seventh literal. The audit row is
  corrected in place rather than left reading FIXED.
- **The dependencies.** `pip-audit` reported **31 advisories across four packages**;
  it now reports none. pillow 12.2.0→12.3.0 (13), setuptools 65.5.0→**83.0.0** (4),
  aiohttp 3.14.1→**3.14.3** (3, then a fourth surfaced at 3.14.2), cryptography
  49.0.0→50.0.0 (1).
- **`setuptools` is newly PINNED, not merely bumped** — and that is the transferable
  lesson. Unpinned, the lockfile said nothing about it, so the audited version differed
  between this machine and the CI runner; a four-CVE package sat unnoticed inside a
  lockfile whose entire purpose is reproducibility. **Pin what the audit can see, not
  only what the app imports.** (The July branch proposed 78.1.1, which is now itself
  below the fix line for PYSEC-2026-3447.)
- **cryptography 49→50 was the only real risk**, since prod's proxy holds the Schwab
  OAuth session and no unit test proves that stack still loads. Checked before promoting:
  authlib + its requests integration, `schwab.auth`, `schwab.client`, oauthlib and
  requests_oauthlib all import cleanly; RSA sign/verify and a PKCS8 PEM round-trip both
  work; schwab-proxy's own 98 tests pass. The only upper bound on cryptography anywhere
  is `curl_cffi<47.0`, which lives in its **dev and test extras** and is not installed.
- **CI is deliberately left non-blocking.** `docs/CI.md` invited flipping the `audit` job
  to blocking "once the baseline is clean", and that condition is now met — but flipping
  means a newly-disclosed transitive CVE halts merges, which is an operator decision, not
  an automatic consequence of clearing the baseline. The flip is one line whenever wanted.
- **⚠ A clean dev told us nothing about prod, and that is the sharpest lesson here.**
  With the four packages fixed, the dev venv audited clean — while **prod was still on
  pip 24.0 with six advisories**, purely because the two venvs were created at different
  times and nothing pinned `pip`. It surfaced only because prod's venv was audited
  separately after the promote. `pip` is now pinned too (26.1.2), both environments
  report no known vulnerabilities, and the rule generalises: **audit every environment,
  and pin every tool the audit can see.**
- **Every suite in the repo was re-measured on the new pins** and the CLAUDE.md Tests
  section now carries all of them. The five previously marked *unverified* had drifted
  badly (driver_svc 162→239, `shared/bus` 15→25); four suites — market_svc, `shared/tests`,
  `tests`, `tools/tests` — had never been listed at all.)

**Prior — 2026-08-19** (**`promote.bat` half-completed a live promotion, and the fix
for it had been written four days earlier on a branch nobody merged.**
- **The defect.** `call stop_all.bat` and `call start_all_wt.bat nowindow` are bare
  names, and cmd will not search the working directory when
  `NoDefaultCurrentDirectoryInExePath` is set — which it is in an automation shell.
  A failed `call` **does not abort the script**, so the promotion took the worst
  possible shape: both guards passed, the stop never ran, `git pull --ff-only`
  **succeeded**, and the restart never ran. Prod was left with new code on disk, old
  code in memory, and its proxy down with nothing behind it to bring it back.
  Recovery was a manual `.\stop_all.bat` then `.\start_all_wt.bat nowindow` with
  explicit path prefixes, verified by an HTTP probe of all eight ports.
- **The fix is the path, not the cwd.** `cd /d "%~dp0.."` at the top of promote.bat
  does make the repo root current — and current is exactly what cmd refuses to
  search. Both calls are now `call "%~dp0..\<name>"`.
- **⚠ The real lesson is about delivery, not batch files.** This exact bug was
  diagnosed and fixed on **2026-08-15 in `29e00d0`**, complete with a whole-repo
  test — on branch `claude/dashboard-design-update-aaae7a`, which was **never merged
  into `Using_Highcharts` or `main`**. So it never reached dev, never reached prod,
  and the identical failure recurred four days later against a fix that already
  existed in the object store. `git merge-base --is-ancestor 29e00d0 main` answers
  NO. **A commit on an unmerged branch is not a fix**, which is the same rule the
  development section states for features and is evidently worth restating for
  repairs. That commit's guard — `tools/tests/test_batch_call_paths.py`, which
  covers every `.bat` in the repo rather than the four launchers, matches `call foo`
  with no extension, and excludes `call :label` — was **recovered from it and
  merged** rather than rewritten, since it was the better of the two.)

**Prior — 2026-08-18** (**The Desk — a single-screen home page, and the first
Tier-2 change the webgui has needed in a while.**
- **What shipped.** `/desk`, pinned alone at the top of the rail and now the target of
  `/`. It aggregates the highest glance-value element of every page into the four
  questions a session opens with: *what is the market doing · where is the structure ·
  what should I act on · what am I holding.* Nine cache views, ONE batched 2 s
  `read_versions`, read-only with click-through to the owning page. **No Highcharts
  anywhere** — nothing on it is a time series, and a permanently-open page is the last
  place you want an element that collapses when it mounts hidden. Detail in
  [webgui-routes.md](webgui-routes.md#desk); design in
  [`plans/2026-08-18-desk-home-dashboard-design.md`](plans/2026-08-18-desk-home-dashboard-design.md).
- **The Tier-2 half.** `cache:options:matrix` rows gained `call_wall`, `put_wall`,
  `net_gex`, `atm_iv`, `iv_state` and `dealer_regime`. All six come from data
  `build_matrix` already had in hand — `net_total` was literally already in the loaded
  row tuple — and all degrade to `None`/`"na"`, never `0`. Additive, so `MatrixSnapshot`
  needed no contract change. This was necessary because `cache:options:gamma` holds
  **one symbol at a time and is mutated by whichever Gamma page is open**, so a second
  page reading it is a race; the Desk needs four symbols at once.
- **A new one-row grid loader, because the obvious call was a trap.**
  `gex_history_db.latest_grid_row` exists so the matrix build does not reach for
  `load_date_with_grid(...)[-1]`, which decodes EVERY grid in the session. Running that
  per symbol per minute across ~45 symbols would have re-created the documented largest
  CPU burn in the options service. A test pins the decode count at exactly one: an
  independent review showed the whole-session version is otherwise **behaviourally
  indistinguishable** to the suite.
- **`iv_regime`'s docstring was five days stale, and it cost two columns.** It said ATM
  IV was *"the axis the app does NOT yet emit"*. The `atm_iv` column landed 2026-08-13;
  measured 2026-08-18 it is **100% populated — 162,566 of 162,598 `gex` rows, 92
  symbols**. That claim was believed twice — once by a codebase search, once by this
  design's first draft, which dropped the IV column on its authority — before one
  `COUNT(*)` settled it. So the Opportunity Board carries IV level **and direction**,
  and `dealer_regime` can reach all six of its labels instead of collapsing to mostly
  `neutral` (`gamma_cascade` and `vanna_squeeze` are exactly the two that need this
  axis). **Verify against the data, not the prose.**
- **Three NaN bugs, all in sketches that read as correct.** `min(hi, nan)` returning
  `hi` is documented here; the same class kept reappearing wearing different clothes.
  `_wall_dist_pct(nan, …)` returned `nan` because `nan <= 0` is False and
  `round(nan, 3)` does not raise, so the `except` never fired — a float that survives
  `is not None` and serialises as invalid JSON. `_latest_atm_iv` rejected NaN only *by
  accident* (`nan > 0` is False) while letting `inf` through as a real IV level. And in
  `structure_positions`, `max(0.0, nan)` returns **`0.0`** — argument order decides —
  which would have drawn spot **exactly on the put wall**, the most alarming thing that
  bar can say, out of missing data. All three are now guarded at the call site and
  pinned by tests.
- **`gamma_walls` filters `None` out of its pair**, so a one-sided chain returns a
  ONE-element list and the prescribed `walls[0]`/`walls[1]` unpacking would have filed a
  **call** wall as a **put** wall, silently, for every such symbol. Now classified
  against spot instead, with a one-sided-grid test.
- **Verified live in dev, and the off-hours case is not hypothetical.** On real 2026-08-15
  data the two index symbols published `net_gex == 0.0` **exactly** (overnight OI zeroing)
  with absurd walls to match — `$SPX put_wall=3000` against spot 7785, `$NDX
  put_wall=14000` against spot 30046 — while SPY and QQQ carried real values. End-to-end
  through the real cache and the real page, both index rows withheld their walls and both
  ETFs kept theirs. That exact-zero is what the suppression turns on, so it was worth
  confirming rather than assuming.
- **CLAUDE.md's test baselines were stale and one was self-contradictory** (the command
  block claimed 2 options_svc failures twenty lines above a section saying "none"). Now
  measured: webgui **1842**, options_svc **1148**, options-scanner **1454/11**. Also
  recorded a **second** source of that suite's skip drift, which the file attributed
  solely to `test_gex_collector*`: three Tk-dependent tests race on root creation and
  **whichever loses self-skips, a different one each run** — so the skipped SET must be
  compared, not just its count.
)

---

**Prior — 2026-08-18** (**Momentum's Align panel now lists the names, not just
the count.**
- **The gap.** Section 2's green panel printed *"24 stocks whose industry and sector both
  confirm — the highest-conviction rows on the page, take these to Trade Analyzer"* and
  then named none of them. It was the only number on the page that says the three levels
  tell **one** story, and the reader had no way to find out which rows it meant. The
  leaderboard could not answer it either: its Align column is three glyphs on a
  top/bottom-15 slice, so a name aligned at rank 40 never appears.
- **`momentum_view.aligned_names(levels, head=None)`** returns that membership, ordered by
  rank (rankless rows last), each row reduced to what a chip needs — symbol, label, score,
  rank, sector, industry. **`alignment_count` is now `len()` of it**, deliberately: the
  figure above the chips and the chips beneath it come out of ONE filter, so a later change
  to the alignment rule cannot move only one of them. `head` splits a visible run off the
  front and reports the rest in `more`, but the page passes no head — all 24 render. A list
  you have to expand is not a list you act on, and the whole point of the panel is the
  handoff to Trade Analyzer.
- **Picking one switches the level.** The chips reuse the page's existing `_name_chip`, so
  they click through to section 4 exactly as the quadrant chips do. But an aligned row
  exists **only at the stock level**, and `example_row` falls back to the leader for a
  symbol that is not on the current level — so clicking SNOW from the Industries view would
  have silently decomposed the top industry instead. `_select_aligned` sets the level first
  and the pick second (the `level_sel.value` assignment fires `_set_level`, which clears
  the selection — order is load-bearing).
- **Chips gained a `sector · industry` tooltip**, in `_name_chip` rather than at the align
  panel, so the quadrant chips get it too — a bare ticker does not say what it is and the
  row already carried both.
- **Verified live in dev, not merely green**: 24 chips against the panel's 24, ordered
  SNOW · PANW · CRWD · ILMN · FTNT (ranks 2/3/5/6/7 in the live payload); clicking SNOW
  from the Industries view flipped the selector to Stocks and repainted section 4 to
  *"Selected · Information Technology · SNOW · 1.79 · 3 of 3 align"*, with the selection
  ring on both the align chip and its Weakening-quadrant twin. webgui suite **1842 passed**.
)

**Last updated:** 2026-08-17 (**The live Market Trend gauge no longer reads near-maximum
bullish on a data outage — `_finite_score_price`.**
- **Same trap as `_finite_pcts`, other sub-score.** `scoring/intraday_trend._clamp` is
  `max(lo, min(hi, v))` and `min(hi, nan)` returns `hi`, so a NaN indicator pins the HIGH
  bound instead of degrading. Measured against the live scorer with all five price inputs
  non-finite: **92.50 at confidence 1.0** through `compute_intraday_trend` (the LIVE Day
  gauge — its `vwap_pct` is live) and **82.50 at the unchanged 0.333** through
  `_structural_trend` (which hardcodes `vwap_pct=0.0`), against a sane structural read of
  ~56–58 at that same 0.333. The confidence gave the reader **no warning at all**.
- **ONE shared guard, applied twice.** `compute._finite_score_price(...)` has
  `score_price`'s exact signature and is now the module's ONLY caller of it — both the live
  gauge (line ~521) and `_structural_trend` (line ~1329) go through it, so a later
  "consistency" edit cannot re-introduce the bug on one side. Each direction input
  (`align_pct` 0.50 / `vwap_pct` 0.20 / `macd_hist` 0.15 / `rsi` 0.15) is replaced by the
  value that zeroes its term and its weight is withheld from the confidence; `adx` is a
  MAGNITUDE scaler, so a missing one substitutes 0.0 (flooring `adx_factor` at 0.3,
  collapsing the needle toward 50) and leaves confidence alone. All five non-finite →
  `TrendSub(50.0, 0.0)`, which drops the 45%-weighted price input out of `blend_trend`.
  ±inf, `None` and junk are handled identically; it never raises.
- **After:** the live gauge reads **50.0 / 0.0** and `_structural_trend` **50.0 / 0.067**
  (the surviving 0.0 vwap is real information). End-to-end on the canned bull fixture the
  price sub-score went **93.9 at confidence 1.0 → 59.9 at confidence 0.0** (the 59.9 is
  step 1b's session-structure blend, which is still a genuine reading).
- **Finite input is bit-identical BY CONSTRUCTION** — an all-finite call early-returns the
  untouched `score_price`, never reaching the substitution branch. Verified anyway:
  **302,944** finite comparisons (200k randomized incl. ±0.0/1e±12/1e±300, a 12⁴×4
  exhaustive grid over the clamp boundaries, 20k int-input calls), **0** mismatches; plus
  **30,003** comparisons pinning the `_finite_pcts` refactor onto the shared `_as_finite`.
- **Mutation-tested**: bypassing the guard turns 5 of the 7 new tests red; reverting call
  site A alone reds `test_live_day_gauge_...`, call site B alone reds
  `test_structural_trend_...` — each side independently covered.
- **`_clamp` itself was considered and rejected** as the fix site; the reasoning is now a
  standing note in CLAUDE.md (nine private copies; no single right answer inside the
  primitive).
- **Reachability is narrower than the write-up assumed, and that is worth recording:**
  `technical.calculate_rsi` / `calculate_adx` **cannot** return NaN — both end in an
  explicit `pd.isna` fallback (50.0 / 20.0) — `calculate_vwap` returns `None` (→ the
  `else 0.0` branch) and `alignment_percentage` is built from ±1/0 scores. Probed live, the
  only NaN that reaches `score_price` today is `macd_hist`, which is already benign
  (`nan > 0` and `nan < 0` are both False → `m = 0`). So this is defense-in-depth against a
  future change to those fallbacks, not a live outage — unlike the sector side, which IS
  reachable via `_fetch_closes`' bare `float(c)` + `_pct_change_n`.
- Tests: `services/sentiment_svc` **293 passed / 1 failed** (up from 286/1), the failure the
  documented `test_compute_regime.py::test_daily_history_wins_over_session_latch`.)

---

**Last updated:** 2026-08-17 (**The pushed Market Snapshot's "Market Read" rebuilt as the
Market Regime Console.**
- **The complaint was right and the cause is structural.** `services/options_svc/market_snapshot.py`
  is an INDEPENDENT RENDERER — not a screenshot — so `/sentiment`'s rebuild into the Market Regime
  Console left it untouched, silently, because nothing fails when it drifts. It was last aligned on
  2026-08-16 (`d946ceb`) and was one whole design behind: ring + sparkline + one-line explainer per
  panel, where the app now shows Sentiment / Trend / Signals cards over a regime block.
- **New module `services/options_svc/market_console.py`** carries the mirror. It cannot import the
  page (Tier 1 imports `nicegui`), so the two are kept in step by CONTRACT, and that module IS the
  contract: every constant and pure decision names the Tier-1 function it mirrors —
  `pages/console.py` (bands, meters, tag splitting), `console_cards.py` (hero/delta/tones/
  divergence), `console_regime.py` (regime colours, sparkline, change text), `regime_mix.py`
  (session slicing, ranked rows, callouts, lead margin), `console_dial.py` (the confidence dial),
  `console_page.py` (chips, order, footer), and `config/theme.toml [console]` (the palette, hard-
  coded as `PALETTE`). This widens the pattern that already existed for three ring constants to the
  whole screen, which is what actually drifted.
- **A units bug went with it.** The old Sentiment panel put the **0-10 composite ("5.0")** in its
  ring centre while its own WEEK/MONTH legend read **0-100 (57 / 53)** — three numbers on one dial
  on two scales. The hero, the meters and the ruler are now all 0-100, and the 0-10 composite
  survives only inside the bias pill (`NEUTRAL 5.04`), labelled by the word beside it. `to100` lives
  in exactly one place (`sentiment_arcs`).
- **Threaded through:** `composite_at` (handlers → `push_notify.send_market_snapshot` →
  `market_snapshot_doc`) for the DATA AS OF chip — the only thing in a still image that says whether
  the numbers under it are current. Nothing else new was needed; `derived`, `snaps` and
  `regime_history` already carried everything the console reads.
- **Dropped rather than faked**, because their meaning is an INTERACTION a PNG cannot offer: the
  `COMPONENTS →` / `TREND DETAIL →` popup links, the header dot's pulse animation (drawn as a static
  dot with the same glow), and the busy/refresh overlay. The Signals meta reads `4 READS`, not
  `4 READS · LIVE` — freshness belongs on the DATA AS OF chip in a still frame. The old push's
  intraday sparklines went too: they were never part of the console (on `/sentiment` they live below
  it in their own charts), and the Day/Week/Month meters plus the vs-WEEK / vs-MONTH deltas carry
  that story. `intraday` stays in the signature; the handler still caches it.
- **One deliberate ADDITION to the mirror:** the regime `transition` line, which `/sentiment` dropped.
  A page can be re-read at any moment; a 30-minute image is one glance, and "mid-flip" is worth its
  one line. It renders DISPLAY words — a `mean_reversion → trending` reaching a phone is the bug the
  regime rename exists to prevent, and it is what this line used to say.
- **Two traps hit while building, both invisible in the HTML string.** (1) A blanket
  `.cn span{display:inline-block}` sits at specificity (0,1,1) and out-ranks every single-class rule
  after it, so `.cn-sname{display:flex}` silently lost and the share rows stacked their swatch above
  the name. (2) `DISPLAY_FONT` is interpolated into an SVG `font-family="…"` ATTRIBUTE, so a DOUBLE
  quote in the stack terminates it early — the dial's regime name rendered in the default face until
  the stack was respelled with single quotes. Both were caught only by rendering.
- **The display face is local-only, deliberately.** Tier 1 loads Rajdhani from Google Fonts; a
  render-blocking `<link>` inside a scheduled push turns one DNS hiccup into a snapshot that degrades
  to a text caption. Measured headless ("TRENDING", 40px/4px tracking): remote Rajdhani **186** css
  px, local **Bahnschrift 214**, Segoe UI **231** — so `Rajdhani,Bahnschrift,'Segoe UI Semibold',…`
  keeps the condensed feel with no network at all.
- **Measured, prod db-0 live caches → PNG at `DOC_WIDTH` 1450.** Before: 585,739 bytes, 2900x4898.
  After: 2,102,755 bytes, 2900x6338 (3169 css px) — the console block adds ~720 css px and the cap is
  `push_notify._MS_MAX_BYTES` 7,500,000, so ~28% of budget. A degraded render (no history, no prior
  session, a zero-confidence horizon) was verified separately: NO READ hatches, em-dash dial,
  "Waiting for regime…", em-dash callouts — nothing fabricated anywhere.
- **Tests:** new `test_market_console.py` (**57**) pins the bands, the no-read path, the scale, the
  ranked-share maths, both SVG builders and the assembly order; `test_market_snapshot.py` rewritten
  (**19**) around the board, the horizon values and the ctx. The 2026-08-16 panel builders
  (`gauge_svg`, the value `sparkline_svg`, the stacked `regime_mix_svg`, the three `*_panel_html`)
  were DELETED and their tests went with their subjects. `services/options_svc` **1148 passed /
  0 failed**, against a **1102 / 0** baseline taken the same day.

---

**Last updated:** 2026-08-17 (**Documentation + `.claude` sweep after the four rebuilds, the
cleanup and the promote.**
- **CLAUDE.md test baseline refreshed: webgui 1356 → 1826**, re-measured today. Noted that the
  count FELL from 1912 because ~86 tests were deleted with the subjects they pinned — the one
  case where a dropping count is the healthy signal, and worth saying out loud next to a
  standing instruction to compare the failing SET rather than the count.
- **The four `2026-08-17-*` design docs are indexed** in CLAUDE.md's Design/plan list, which they
  were not.
- **New durable gotcha recorded: `vector-effect` is not in DOMPurify's allowlist**, which makes
  the standard `viewBox` + `preserveAspectRatio="none"` idiom a trap — geometry survives, strokes
  render thick horizontally and hairline vertically, and the server-side string stays correct so
  no test can see it. Both supplied designs used that idiom; it was caught twice before shipping.
  The fix (percentage-addressed `<line>`s, no viewBox) and the `<polyline points>` caveat are
  written next to the existing `dominant-baseline` story they rhyme with.
- **The four rebuilt screens are documented as one design family** with `rotation_view` as the
  palette root and `sector_heat`/`rrg_view`/`momentum_view`/`oklch` as its siblings.
- **Three restart traps written down**, all hit today: a failed bind is SILENT and leaves the old
  server serving stale code (`Errno 10048` in the launcher log is the tell); `netstat`-and-taskkill
  one-liners lose a `$`-anchored regex to the shell and report success having killed nothing; and
  dev serves the DEV CHECKOUT, so uncommitted worktree changes look exactly like a broken restart.
- **`.claude/launch.json` gained a `webgui-dev` configuration on :9500.** The single `webgui` entry
  named :8500 — PROD's port — and `autoPort:false` means the entry must match what the checkout
  actually binds. A worktree has no `env.local.toml`, resolves to prod, and binds :8500 where the
  live stack already is. Both configurations now exist and CLAUDE.md says which to pick.
- **`.claude/settings.json` allow-list gained the absolute worktree venv path** for pytest/ruff —
  every test run from a worktree uses it, and only the relative forms were listed.
- **Technical Reference gained the display arithmetic** for the rotation gauge, the flow band, the
  RRG domain/marker/trail maths and the Momentum bars, since that manual answers "where does this
  number come from".
- **⚠ NEW KNOWN ISSUE, found while checking a comment I had written: Sector & Industry and Sector
  Rotation can print OPPOSITE regime verdicts.** They read different quantities on different
  scales — `sector.rotation.day_spread` (cyclical minus defensive daily % RETURN, bands ±0.3/±1.0)
  versus `assessment.headline.spread` (mean RS-MOMENTUM spread, threshold ±1.5). Measured live:
  **+0.37 → "Risk-on regime"** on one tab, **−1.52 → "Risk-off"** on the next. It predates the
  rebuilds — the old `rotation_banner` had the same split — and is recorded in CLAUDE.md with the
  warning NOT to "align the thresholds", since the numbers are not commensurable. A real fix is a
  product decision, not a refactor.)

---

**Last updated:** 2026-08-17 (**Dead-code cleanup, part 2 — `pages/sentiment.py`.** The
deferral recorded in the entry below is now closed: the module is dead-code-free.
- **`pages/sentiment.py` 1355 → 948 lines**, 96 top-level symbols → **43, all reachable**.
  Measured the same way (ast reference graph, reachable from the real roots — `render`,
  `sector_table_rows`, `industry_rows`, the only three names any non-test file imports, confirmed
  by a **repo-wide** scan of 705 files). Verified afterwards that the module has **zero**
  unreachable symbols left.
- **Six clusters went, each traceable to a specific earlier change.** The old sectors-table
  helpers (`pct_color`/`pct_text_class`, `pcr_*`, `rrg_color`/`rrg_text_class`, `rotation_banner`,
  `rotation_text_class`, `sector_summary`) — stranded by the 2026-08-17 heat-grid rebuild. The
  regime helpers (`regime_headline_parts`, `regime_transition_text`, `regime_label`,
  `regime_direction`, `regime_evidence_rows`, `_REGIME_*`, `_DIRECTION_TEXT`,
  `REGIME_TEXT_CLASSES`) and the tone/tile set (`_tone_classes`, `TONE_*`, `_TONE_HEX`,
  `_TILE_FLOOR`, `_mix`, `_hex_rgb`, `_rgba`) — superseded by the **Market Regime Console**. The
  leftover palette (`CLR_CYAN`/`CLR_FLAT`, `TXT_CY`/`TXT_FLAT`, `BG_*`, `SENT_TEXT_CLASSES`,
  `_HEX_TO_TXT*`, `TRAFFIC_BG_CLASSES`, `BORDER_R`). Assorted colour helpers (`bias_color`,
  `bias_text_class`, `trend_text_class`, `traffic_bg_class`, `_TREND_STATE_CLASS`, `_TONE_TXT`),
  and `sentiment_avg` / `velocity_lines`.
- **⚠ Three carried an explicit "retained for display/test parity" comment** — `pcr_from_chain`,
  `week_month_from_closes`, `is_rth` — kept when the compute moved to `sentiment_svc` in the
  3-tier migration. Deleted anyway, and the reason is worth recording: **no test ever compared
  them to the service's implementation**, so they were not providing parity, only testing a
  second copy in isolation while the display that used them was removed. That is precisely the
  "tests keeping dead code green" trap. If real parity coverage is wanted, it belongs in a test
  that exercises both sides, not in a duplicate transform.
- **Two now-unused imports** dropped with them (`pages.rings.ring_svg`, and the four
  `pages.regime_mix` re-exports) — the headline helpers that used them were the last consumers.
- **~40 tests removed with their subjects**, plus two orphaned fixtures. `test_sentiment.py`
  716 → 698, `test_sentiment_sectors.py` → 156. Suite 1866 → **1826 passed, 0 failed**.
- **Verified beyond the suite:** all six render paths — **`/sentiment` itself**, rotation, rrg,
  sectors and momentum at both levels — smoke-rendered in-process against the real cached
  payloads. `/sentiment` matters most here: it is the page this module exists for, and 404 of
  its 1355 lines just went.)

---

**Last updated:** 2026-08-17 (**Dead-code cleanup after the four screen rebuilds.** The Highcharts
builders the rebuilds replaced, plus everything only they reached, removed from
`pages/sentiment_rotation.py` and `pages/sentiment_momentum.py`.
- **Found by reachability, not by memory.** A throwaway script parsed each module with `ast`,
  built a symbol→symbol reference graph, and marked everything reachable from the real roots
  (`render`, plus any name a NON-test file imports). Two earlier passes were wrong and are worth
  recording: a plain grep counted *same-named symbols in other files* as references (both modules
  define `quadrant_label_bands`), and a "used anywhere in the file" check kept helpers alive that
  only dead code called (`_sector_trace` exists solely for `rrg_scatter_figure`). Only transitive
  reachability from real roots gives the true set.
- **`sentiment_rotation.py` 562 → 393 lines.** Gone: `rrg_scatter_figure`, `_sector_trace`,
  `quadrant_label_bands`, `_QUAD_LABEL_STYLE`, `_hex_to_rgba`, `headline_parts`, `side_rows`,
  `rotation_rows`, `quadrant_color`/`quadrant_text_class`, `regime_text_class`/`_regime_color`,
  and the whole local `CLR_*`/`TXT_*`/`_HEX_TO_TXT`/`SENT_TEXT_CLASSES` palette. What survives is
  `render` plus its five style constants and `DEFAULT_RISK_THRESHOLD`.
- **`sentiment_momentum.py` 943 → 756 lines.** Gone: `quadrant_figure`, `ribbon_figure`,
  `ribbon_subset`, `_latest_rank`, `quadrant_label_bands`, `_zero_line`, `ZERO_LINE_*`,
  `_GRID_COLOR`, `_SERIES_COLORS`, `_QUAD_LABEL_STYLE`, `banner_parts`, `banner_reasons`,
  `BANNER_CLASSES`, `status_text`, `_accel`. The leaderboard's transforms all survive — the board
  is still on the page behind its expander.
- **46 tests removed with their subjects**, plus four orphaned fixtures (`_assessment`, `_head`,
  `_sector_traces`, `_hist`) and one vacuous guard: `test_rrg_page_caption_removed` asserted a
  caption string was absent from a page that has since been rewritten end to end, so it could
  never fail again. Suite 1912 → **1866 passed, 0 failed**.
- **This closes the "two quadrant palettes" question** the Rotation redesign opened.
  `sentiment_rotation.quadrant_color` was the second palette; its only consumers were the
  Highcharts RRG scatter and the Sector & Industry RRG column, and both went in those rebuilds.
  There is now ONE palette — `rotation_view.QUAD_HUE`/`QUAD_CHROMA`, imported by `rrg_view` and
  `momentum_view`. CLAUDE.md's warning is rewritten accordingly.
- **Verified beyond the suite:** all five render paths (`rotation`, `rrg`, `sectors`, `momentum`
  at both levels) smoke-rendered in-process against the REAL cached payloads, because static
  reachability cannot see a name reached by string or `getattr`.
- **⚠ NOT done, deliberately: `pages/sentiment.py` has ~53 unreferenced top-level symbols** by the
  same measurement — the same four rebuilds stranded them. Left alone because some predates this
  work, several are still covered by `tests/test_sentiment_sectors.py`, and cutting ~50 symbols
  out of a shared 1000-line module deserves its own change. Recorded in CLAUDE.md's Tests
  section.)

---

**Last updated:** 2026-08-17 (**Momentum rebuilt as a guided page.** `/sentiment/momentum` rebuilt
from a supplied design (`Momentum.html`), the fourth screen from that project. Design:
[design](plans/2026-08-17-momentum-guided-page-design.md); per-page detail in
[webgui-routes](webgui-routes.md).
- **A pure Tier-1 re-render — `sentiment_svc` untouched.** Checked the design against the live
  payload first: every element binds to `cache:sentiment:momentum` as it already exists, down to
  the five component z-score keys and the `dispersion_pct` of 0.1176 the design renders as "12th".
- **The page became a numbered argument** rather than a dashboard: (1) is momentum worth trading
  today — **all three regime states side by side** with the live one enlarged and each carrying an
  instruction, then dispersion as a percentile + bar; (2) three levels' top-quartile counts on
  **√-scaled** tracks plus the count of stocks where industry AND sector confirm; (3) the four
  quadrants as counts + chips; (4) the **top-ranked** row decomposed into diverging z-score bars;
  (5) rank over recent sessions. Then **limits cards** that state on the page the four ways this
  screen can be confidently wrong.
- **Showing all three regime states at once is the point of section 1.** The old banner named the
  live state and left the reader to remember what the other two meant — but the premise of the page
  is that momentum only pays in some conditions, which is a comparison, not a label.
- **The leaderboard survives behind a COLLAPSED expander** (the page owner's call). Deleting it, as
  the design does, turns a screener into an orientation page with nowhere to see which names to act
  on; leaving it open makes the argument above it read as preamble.
- **The example row is the top-ranked row**, deterministically — so the anatomy card and the
  leaderboard's first line are always the same name, i.e. "the current leader, explained".
- **⚠ Three design assumptions that would have shipped broken**, each caught against live data.
  (a) **`rank_history` is ragged** — symbols carry 15/10/7/**5** sessions, and the design's
  `i/(len-1)*100` would stretch a five-session symbol across the full width as a full-length trend;
  every series now shares ONE date axis (verified: AWAY has 10 of 15 and starts at x=35.7%).
  (b) **The rank domain is computed, not capped at 21** — live ranks reach 61 (industries) and 272
  (stocks), and the most interesting name is usually the one climbing from deep: today **GDX
  60th→23rd** and **TER 272nd→52nd**, both cut off entirely by a 21-deep window. The bottom tick is
  always labelled, so a 1…272 axis cannot read as ending at 205. (c) **`vector-effect` is stripped
  by DOMPurify** (verified) — the design's `<polyline>` in a scaled viewBox relies on it; `points`
  cannot take percentages either, hence percentage-addressed `<line>`s.
- **Scatter and ribbon dropped.** `quadrant_figure` / `ribbon_figure` / `ribbon_subset` /
  `quadrant_label_bands` / `_zero_line` are now **dead**, kept only because their tests pin them —
  same as the RRG's `rrg_scatter_figure`. Worth one cleanup pass across both pages.
- **Pure/impure split:** the new arithmetic in `webgui/pages/momentum_view.py` (59 tests); the
  leaderboard's own transforms stay in the page module because the board does. Webgui suite 1905
  passed, 0 failed.
- **Live-verified** at 9500: all five sections against the live payload — Neutral · now with the
  21/63 lookback interpolated, dispersion 12th, levels 3/11 · 17/69 · 74/296 with tracks
  19.3/48.3/100%, 26 stocks aligned, quadrants 9/17/24/19 summing to 69, ARKG decomposed with
  Trend/RS clamped at +3.00, 65 rank-line segments in two stroke widths. Leaderboard collapsed by
  default and opening to 2×15 rows whose first row matches the example card. Level switch verified
  via `?level=stock` (heading → 296 stocks, align text appears, story → TER). One cosmetic fix on
  the way: the expander header's `uppercase` was inheriting into the table cells.
- **Docs:** design doc, CHANGELOG, webgui-routes, the CLAUDE.md route row, `page_help.py`, and the
  User Guide + Reference Guide Momentum sections.)

---

**Last updated:** 2026-08-17 (**RRG follow-up: smoothed trails + sector-name labels.** Two changes on
top of the rebuild below. Design doc updated:
[design](plans/2026-08-17-rrg-plot-design.md).
- **Trails are smoothed** — each is resampled along a **Catmull-Rom spline** at 6 sub-segments per
  span, so a five-reading trail draws 24 sub-segments instead of 4 straight ones. Measured live, the
  per-segment turn angle within one trail fell from **max 142° / median 27.3°** to **max 8.3° /
  median 4.0°**.
- **Two properties keep it honest as a data plot.** The curve **passes through every real reading** —
  smoothing only decides the route *between* them — and the end tangents are clamped so a trail
  cannot flare past its own endpoints. `TAIL_TENSION` stays at the standard 0.5 with a bounding-box
  test, because a spline that overshoots is claiming the sector visited a position it never held:
  the same class of quiet dishonesty as the fixed domain clipping tails.
- **Side benefit:** with the trail resampled, width and opacity became CONTINUOUS functions of
  position along it rather than stepping once per reading, so the fade reads as one gesture.
- **Cost:** 264 `<line>`s / ~34 KB of SVG for eleven sectors, on a page that repaints only on a
  manual refresh (~8 ms to walk the rendered DOM).
- **Markers are labelled with the SECTOR NAME**, not the ETF code, and moved to the sans face — a
  proper noun set in a mono ticker face reads as a code. A name is ~4× a ticker's width, which broke
  the fixed flip-at-78% rule ("Communication" right of a marker at 70% would hang off the plot where
  "XLC" fitted), so the side decision now **measures the label** against the plot's right edge.
- **Live-verified:** 264 lines, stroke tapering 1.33 → 1.72 → 2.14 and opacity 0.18 → 0.67 across one
  trail; all eleven sector names render in Instrument Sans with **no overflow on either edge**.
  Webgui suite green.
- ⚠ **Process note:** the first verification pass read a STALE server. A previous webgui had kept
  :9500, so the restart failed to bind and exited while the old process kept serving — the page
  showed 44 lines and ETF labels, i.e. the pre-change code. Root cause: the ad-hoc
  `netstat`-and-taskkill one-liners used a `$`-anchored regex inside a double-quoted `-c` string,
  where the shell ate the anchor, so they matched nothing and reported success. Check the port is
  actually free after killing, and read the launcher's log for `Errno 10048`.)

---

**Last updated:** 2026-08-17 (**RRG rebuilt as a hand-drawn plot.** `/sentiment/rrg` rebuilt from a
supplied design (`RRG.html`), the third screen from that design project. Design:
[design](plans/2026-08-17-rrg-plot-design.md); per-page detail in [webgui-routes](webgui-routes.md).
- **Highcharts is gone from this page.** The design is a plain scatter with quadrant washes, a fixed
  crosshair and per-marker sizing; Highcharts brings a scale model, a legend, a tooltip engine and a
  reflow lifecycle none of that needs — against this app's documented list of Highcharts traps.
  Eleven markers and 44 line segments do not justify any of it, and the geometry becomes pure and
  testable instead of living in an options dict. Still Tier-1; no service change.
- **Marker AREA is the sector's S&P weight** (diameter as √weight). A linear diameter would draw
  Technology as ~10× Utilities rather than the ~16× in area it is. The old chart drew every sector
  the same size, so a heavyweight sliding out of Leading looked identical to a 2% sector doing it.
- **Each trail is the last FIVE readings** (as requested), fading AND thinning toward the past — age
  encoded twice, because either alone is ambiguous against eleven overlapping trails and together
  they read as direction without an arrowhead. Confirmed against the payload that `tail[-1]` IS the
  current position, so a trail ends exactly on its marker.
- **⚠ Three departures from the design, each forced by real data and each of the kind that ships
  looking fine and is wrong.** (1) **The domain is computed, not fixed** — the design hard-codes
  RS-Ratio 98.9…101.1 and the real five-reading tails reach **97.28**, clipped clean off the plot
  with nothing to indicate anything was missing. Now derived from the data, padded 8%, floored at
  the design's window, and **kept symmetric about 100** — load-bearing, because the washes and
  crosshair are drawn at exactly 50%/50% and an asymmetric window would put the axes somewhere other
  than 100/100 and silently reassign every sector's quadrant on screen. (2) **The trails are real** —
  the design generates a spiral with `sampleTail()` and says so in its own footer. (3) **No
  `vector-effect`** — the design scales a 0–100 viewBox with `preserveAspectRatio="none"` and leans
  on `vector-effect:non-scaling-stroke`; **that attribute is not in DOMPurify's allowlist**, so
  `ui.html` strips it and every trail renders thick horizontally and hairline vertically, with the
  server-side string still perfectly correct. Percentage coordinates on the `<line>`s need neither
  the viewBox nor the rescue. Guarded by `test_tail_svg_emits_nothing_dompurify_would_strip`, the
  same invariant `rings.py` carries for the same reason.
- **Labels are decluttered per side** — eleven sectors cluster hard around 100 (four within 3% of
  each other vertically, measured live), so without it the ETF codes overprint into a smear. They
  flip to the marker's left past 78% of the width. Per side, because a left label cannot collide
  with a right one.
- **A tinted verdict strip** above the plot carries the regime, its sentence and the arithmetic, so
  a reader who takes nothing else away still leaves knowing which way the rotation points.
- **Palette imported from `rotation_view.py`** — same quadrant hues, same warm-neutral ladder — so
  the two rotation tabs cannot drift apart.
- **⚠ `sentiment_rotation.rrg_scatter_figure` / `_sector_trace` / `quadrant_label_bands` /
  `_hex_to_rgba` are now DEAD** — nothing imports them but their own tests, both consumers having
  been rebuilt. Left in place rather than deleted unilaterally; worth a follow-up.
- **One existing test rewritten:** `test_render_uses_native_hover_dimming` asserted `ui.highchart`
  appears in the RRG render, which is no longer true by design. Rewritten around its durable half —
  neither rotation page may reintroduce the per-hover client→server round-trip the plotly version
  used.
- **Pure/impure split:** all geometry in `webgui/pages/rrg_view.py` (38 tests). Webgui suite 1836
  passed, 0 failed.
- **Live-verified** at 9500: plot 609×600, **44 trail lines** and **11 markers** sized 13–21px with
  Technology largest; percentage coordinates survive the sanitizer and resolve correctly
  (`x1="3.70%"` → 23px of 609) with `stroke`/`stroke-width`/`opacity` all present; four quadrant
  washes in the right corners; **crosshair at exactly 50.00%/50.00%**; corner labels correct;
  Y ticks 97–103, X ticks 98–102; strip tinted red for Risk-off; no horizontal overflow.
- **Docs:** design doc, CHANGELOG, webgui-routes, the CLAUDE.md route row, `page_help.py`, and the
  User Guide + Reference Guide RRG sections.)

---

**Last updated:** 2026-08-17 (**Sector Rotation rebuilt as a board.** `/sentiment/rotation`
rebuilt from a supplied design (`Sector Rotation.dc.html`), the second screen from the same design
project as the Sector & Industry heat grid below. Design:
[design](plans/2026-08-17-sector-rotation-board-design.md); per-page detail in
[webgui-routes](webgui-routes.md).
- **A pure Tier-1 re-render — `sentiment_svc` was not touched.** Checked first: every number the
  design shows already existed in `cache:sentiment:rotation`, down to the 42.4/57.5 flow totals and
  the four quadrant weights. The design had clearly been authored against this exact payload.
- **A verdict strip** replaces the headline sentence: regime word + tone dot; the cyclical and
  defensive means either side of a **diverging spread gauge** (−3…+3 track, both ±threshold triggers
  ticked, zero marked); and the spread with a derived sentence on how far past its trigger it sits.
  **The fill spans between the reading and zero rather than growing from the left** — the quantity is
  signed, so which side of zero it lands on IS the verdict, and a left-anchored bar would encode −3
  and +3 as "small" and "large" instead of "opposite".
- **A flow band** replaces the Rotating From/Into name lists: one segment per rotating sector,
  `flex-grow` carrying its S&P weight so **segment area is index share**. Both the side wrappers AND
  the segments are weight-grown — growing only the segments would render a 42/58 split as 50/50. The
  split keys on the engine's own `direction` field, not the quadrant, so the band always partitions
  exactly what the assessment called rotating. A segment under 7.5% of its own side drops its label
  rather than clipping it (3 of 11 today).
- **Four quadrant panels** replace the 6-column quadrant map, in rotation reading order (Improving ·
  Leading · Lagging · Weakening), each with its share of the index and a chip per sector carrying
  RS-Momentum and a weight bar. **All bars share ONE scale — the heaviest sector on the page** — so a
  2% sector alone in a quadrant cannot draw the same bar as a 32% one. Every panel renders even when
  empty: a quadrant nobody is in is information, and dropping it would silently reflow the rest.
- **Retired:** the Full Quadrant Map table, and with it the `RS-Ratio` and `Dir` columns. RS-Ratio is
  named on the axis rails and plotted properly on the RRG tab; `Dir` is what the band's two sides
  encode. The `pairs` field was already unused.
- **Three things the design left implicit, now explicit.** The trigger sentence needed a ladder (a
  board that says "just past the trigger" at −1.51 must say something else at −4.0) — inside the
  band / under 1.5× / beyond, with the ratio named in `ENTRENCHED_RATIO` rather than buried. The
  verdict sentence is rewritten rather than echoing the service's log line (`"Risk-OFF rotation -
  money rotating into defensives, out of cyclicals"` repeats the regime beside it and uses a hyphen
  for a dash), falling back to the service text for any regime we cannot phrase. And hairlines are
  gaps, not borders, in both regions — a real border orphans a rule when a panel wraps.
- **New `[rotation]` section in `config/theme.toml`** carrying only the two grounds and the two
  faces. The warm-neutral ladder (one oklch lightness step per role, so the hierarchy is visible in
  the source) and the four quadrant hues are ramps, so they live in `webgui/pages/rotation_view.py`
  — the same call made for the `[sectors]` heat ramp. **`webgui/pages/oklch.py`** now holds the
  oklch→sRGB conversion both pages share.
- **⚠ Two quadrant palettes now coexist, deliberately.** `sentiment_rotation.quadrant_color`
  (green/cyan/yellow/red) still drives the RRG scatter and the Sector & Industry quadrant text; this
  design re-hues Improving to blue 232 and Weakening to olive 80, implemented for this page only.
  Restyling the RRG chart is a change nobody asked for — flagged in CLAUDE.md as an open question.
- **`render()` replaced; every existing builder kept** — `pages/sentiment_rrg.py` imports
  `SENT_TEXT_CLASSES` / `headline_parts` / `regime_text_class` / `rrg_scatter_figure` from this
  module and its tests pin them. **No `ui.add_css`**: the gauge is absolutely-positioned runtime
  percentage arbitraries, the documented continuous-value exception.
- **Pure/impure split:** all arithmetic in `webgui/pages/rotation_view.py` (38 tests in
  `tests/test_rotation_view.py`). Webgui suite green.
- **Live-verified** at 9500 against prod's rotation cache: gauge triggers at exactly 25.00/75.00%,
  zero at 50.00%, fill 24.67%→50% for the live −1.52; band sides 42.1%/57.0% with 8 labelled and 3
  correctly unlabelled segments and per-quadrant hues; 4 panels, 11 chip bars, widest exactly 100%
  (XLK) and XLF at 41.3% = 13.42/32.53; both faces loaded; no horizontal overflow; **RRG tab
  unaffected** (11 series, all labels).
- **Docs:** `page_help.py`, the User Guide and the Reference Guide all rewritten for the new
  controls, plus the CLAUDE.md route row and the theme.toml page-scoped-section paragraph.)

---

**Last updated:** 2026-08-17 (**Sector & Industry rebuilt as a heat grid.** `/sentiment/sectors`
rebuilt from a supplied design set (a README of design decisions + collapsed/expanded screenshots).
Design: [design](plans/2026-08-17-sector-heat-grid-design.md); per-page detail in
[webgui-routes](webgui-routes.md).
- **Magnitude became the primary encoding.** Day / Week / Month are three adjacent filled tiles
  flush to the right edge with the figure inside, so the colour band is continuous across a row and
  down the page. Previously they were signed numbers coloured only by sign, which made the reader do
  the ranking.
- **Intensity normalises PER COLUMN**, across sectors *and* all industries whether or not they are
  expanded — so opening a sector adds rows without repainting the ones above it. Below a per-horizon
  flat band (±0.50 / ±1.00 / ±1.50%) a cell reads neutral, so a quiet month doesn't glow merely
  because a month drifts further than a day.
- **⚠ The scale is the column's p90, NOT its max — a deliberate departure from the reference.** That
  prototype normalises on the max, which works on its *synthetic* industry placeholders because they
  cluster near their sectors. Real industry ETFs have a fat right tail: measured live over the 81-row
  set, one **+27.46%** Month reading against a **3.24%** median pinned all eleven sectors into 4 of
  the 13 steps — destroying the "a column always uses its full range" property the design exists to
  get. Swept 0.80/0.85/0.90/1.00; **0.90 is the highest quantile that still spends every step on
  every column**. Values above it saturate (`heat_level` already clamps).
- **The ramp is oklch** (L 0.175→0.300, C 0.022→0.110, hue 158 up / 22 down), authored in that space
  because the grid lives at the dark end where an sRGB interpolation bunches the low steps. Baked
  into **13 static Tailwind classes**, not a per-datum arbitrary value — the house rule on
  data-driven colour. The figure's colour lifts with its tile.
- **Sortable + ranked.** Day / Week / Month headers sort (click to switch, again to reverse; default
  Day desc), and the `RANK 1 OF 11 · DAY` line under each sector name follows the active column.
- **RRG dropped**; **P/C keeps a plain number** with an amber tint above 1.5. Both for the same
  reason: neither is a percentage change, and sitting them beside a colour band invited reading them
  as a fourth timeframe. The rotation read has two dedicated tabs that show it properly.
- **New `[sectors]` section in `config/theme.toml`** (chrome palette + Instrument Sans / JetBrains
  Mono), with `build_sector_tokens` / `build_sector_font_head_html` following the `[console]` /
  `[macro]` pattern. The heat ramp itself is deliberately NOT config — it is a data-driven cell map,
  the category already excluded alongside the gauge face and the score/heat/P&L zone maps.
- **The page needs NO `ui.add_css` block at all.** A screen that is nothing but colour and
  measurement is the strongest case for the Tailwind-first standard, and it holds: fractional column
  tracks, flush tiles, truncation and the scroll wrapper are all utilities. Only the font `<link>` is
  injected.
- **Pure/impure split:** all arithmetic in `webgui/pages/sector_heat.py` (45 tests in
  `tests/test_sector_heat.py`); `sentiment_sectors.py` is widgets + wiring. Webgui suite green.
- **Live-verified** against prod's sectors cache at 9500: 11 sector rows @66px + 70 industry rows
  @34px, tiles exactly 112px apart, Day band spanning full green → neutral → full red, sorting +
  rank line + amber P/C all correct, both faces loaded, and at a 900px viewport the grid scrolls
  inside its own wrapper with no page-level horizontal overflow.
- **Docs:** `page_help.py` rewritten for the new controls (it claimed an RRG column and a summary
  line that no longer exist), plus the CLAUDE.md route row and the `theme.toml` page-scoped-section
  paragraph.)

---

**Last updated:** 2026-08-16 (**Nav rail + top bar — the NEURALSTRIKE design ported.** A supplied
design set (`Menu.dc.html` 264x764, `Menu Collapsed.dc.html` 68x764, `Top Bar.dc.html` 1400x56)
ported into the NiceGUI shell. Design: [design](plans/2026-08-16-nav-topbar-redesign-design.md).
- **The rail's order became DATA.** It used to be implicit in the sequence of render calls inside
  `_layout`, so reordering the menu meant editing render code. `NAV_SECTIONS` now holds three
  captioned sections — **MARKETS** (Dealer Positioning · Opportunity Board · Flow Alerts · Trend &
  Sentiment) · **STRATEGY** (Strategy Tools · Options · Trade Analyzer · Claude Trades) ·
  **ACCOUNT** (Portfolio · More) — whose entries reference their group/page **by name**, so
  `_NAV_GROUPS`/`OPTIONS_RAIL`/`FLAT_NAV` stay the single source of every label + icon and a typo
  raises at import instead of silently dropping a page from the menu. Caption counts are DERIVED
  from `len(entries)`. All ten rail entries survive; the sentiment group renamed **"Market Trend &
  Sentiment" → "Trend & Sentiment"**. ⚠ Options sits under STRATEGY while Dealer Positioning /
  Opportunity Board / Flow Alerts sit under MARKETS — deliberate: those three are market-WIDE
  reads, the Options group is the per-signal find → analyze → track → repair workflow.
- **Geometry 64/248 → 68/264**, and the open width is now **interpolated from `NAV_WIDTH_OPEN`**
  rather than duplicated as a CSS literal.
- **Captions cross-fade to hairlines when collapsed** — `.nav-sep` is the exact inverse of the
  existing `.nav-title` opacity rule, both absolutely placed in one fixed-height box so neither
  state reflows the other. No JS, no second render path.
- **Live footer status card, costing NO new probe.** It reads the throttled `/health` fan-out the
  2 s watcher already runs, and its warning count **IS** `len(alerts.unhealthy_keys(...))` — the
  same computation behind the System Status badge, so the two cannot drift apart. No probe data
  renders as **"unknown"**, never a confident "Data feed live"; a bus outage resets it rather than
  stranding the last good reading. Latency is the mean of services that ANSWERED, since a timed-out
  probe would report the failure rather than the feed. `display:none` in the rail (not faded — it
  must surrender its height too).
- **Stop All Services became a danger-outlined button** and moved LAST in `SYSTEM_RAIL`: it is the
  one irreversible item in the rail and should not sit where an overshoot from Settings lands.
- **Breadcrumb moved to the LEFT** of the top bar behind a hairline, reading as a continuation of
  the brand, with a `›` caret in place of the 4px dot; the market pill keeps the right edge alone.
- **The design's ⌘K search pill and notification bell were deliberately NOT ported** — a palette
  over ~26 route labels is real work for a rail one hover away, and the bell would duplicate the
  System Status badge. Shipping them as decoration was rejected outright.
- ⚠ **Three rail colours shipped broken and only a live browser caught them.** A Tailwind
  `text-[#…]` is ONE class with no `!important`, so it loses both to `theme.build_nav_css`'s
  `.nav-drawer a{color:<[menu].text>!important}` and to `_NAV_CSS`'s own 3-class
  `.nav-drawer .nav-active .nav-label`. Measured: the danger button's label rendered menu-grey
  `rgb(152,161,192)` (its icon was fine — that already had the 3-class rule), and the AI pill
  rendered white `rgb(238,241,246)` on the ACTIVE row, the only row it ever appears on. Fixed with
  a rule each; **the general rule is now in CLAUDE.md** — any colour on a rail element needs ≥3
  classes + `!important` or it is decorative only.
- **Tests: 1606 green** (was 1587 + 4 failing pinned invariants). Four updated — the group rename,
  the geometry, the `NAV_SECTIONS`-scoped icon check, and the group-reachability guard that used to
  count `_nav_group_link(` occurrences in `_layout`'s source (the drawer now LOOPS, so reachability
  became a property of the data). Fifteen added, the load-bearing one asserting `NAV_SECTIONS` is a
  **partition** — a regrouping that drops or doubles an item is invisible to every other test.
- **Live-verified** on a spare port against the running stack: 68/264, derived counts, the
  cross-fade inverting both ways, badges landing inside the rail, the card reading "6 services ·
  8 ms" with its count equal to the System Status badge, and the breadcrumb rendering
  "Trend & Sentiment › Sector Rotation".)

**Prior — 2026-08-15** (**Options Flow redesign — Premium Divergence + Flow Field.** The
`/options/gamma` **Flow** and **Net Prem** views are replaced by two dark-console panels built to a
supplied spec, in the same visual system as the plasma heatmap. Design/plan:
[design](plans/2026-08-15-options-flow-redesign-design.md) /
[plan](plans/2026-08-15-options-flow-redesign-plan.md).

**What the spec removed, and why it was right.** The floating tooltip sat on top of the data it
described (→ a fixed right rail); the bottom legend duplicated information and, on Net Prem, wrapped
into rows that collided with the rotated time labels — a live bug `net_prem_figure` carried a comment
about (→ status chips above the plot + terminus labels at each line's right end); and the yellow
price line read as a THIRD premium series beside the green/pink pair (→ white `#EAF6FF`, on its own
scale).

**A new stored view: per-strike premium.** The strike ladder needs call/put premium BY STRIKE at the
cursor's timestamp, and nothing held it — `index_call_put_premium` collapses the chain to two scalars,
and `gex_history.db` stores per-strike GEX. New PURE `flow_skew.premium_by_strike(chain)`, written by
`gex_collector.poll_once` as a **fifth view string, `"prem"`** — and that costs **no schema change**:
`snapshots.view` is free-form and a premium cell is `{call, put, net}` floats, exactly the shape the
columnar float32 packer gates on, so `insert_snapshot`/`_encode_grid`/`load_date_with_grid` all work
unchanged. **Cost: +25% on `gex_history.db`** (four views become five). **Forward-only** — the ladder
is empty until `options_svc` restarts and collects, and the panel SAYS so rather than drawing a blank
frame that reads as a real reading. Its own try/except in the collector: a premium failure costs the
ladder, never the four Greek rows the heatmap is built on.

**Tier 2** — `compute.prem_ladder` crops each row to ±5 strikes around **that row's own spot** (a
session-wide centre would slide every earlier ladder off the money on a trending day), published
additively on the gamma snapshot as `prem_ladder`. Read through the SAME `_history_rows_incremental`
memo as the Greek views — it is generic in the view string, and this read has exactly the property
the memo exists for.

**Tier 1 — the panels leave Highcharts.** Hand-rolled SVG strings mounted with `ui.html` and updated
via `el.content` (the `rings.py` / `regime_mix.py` idiom), for three reasons: the spec IS SVG
(hairline strokes over a translucent halo, a per-segment two-tone ribbon, decluttered terminus
labels — each a fight in Highcharts and a straight string build in Python); the repo already has the
idiom twice; and it sidesteps both documented `ui.highchart` hazards at once — the ESM-import-map
trap and the `chart.update()` merge leakage that made `_set_chart` recreate the element on a Flow ↔
Net Prem switch. **The whole panel — chart, chips, ladder, rail — is ONE raw HTML fragment**, the
documented out-of-scope case for the Tailwind-first standard (as the Calculator's P&L heatmap and
the Gamma Explain block already are), because everything moves together under one cursor and the
scrub is client-side; the one thing that genuinely cannot be inlined (the pulse keyframes) is
`theme.FLOW_KEYFRAMES_CSS`, this page's ONE `ui.add_css` addition.

**The scrub is fully client-side** — mousemove over either plot updates the chips, rail, ladder and
leaderboard with no server round-trip, and leaving returns to the session's latest reading.
Coordinates are computed SERVER-side and shipped in the payload rather than re-derived in JS: two
implementations of one mapping, and the first symptom of their drifting apart is a cursor dot sitting
off its own line.

**⚠ The sanitizer constraint is load-bearing and cost a real defect once already.** `ui.html`
sanitizes through NiceGUI's bundled DOMPurify (it monkeypatches `Element.prototype.setHTML`), whose
allowlist is NOT the native sanitizer's — `dominant-baseline` is STRIPPED, which silently
mis-positioned every label on the sentiment rings while the server-side string stayed correct and the
suite stayed green. So these panels use `dy="0.35em"`, no `foreignObject`, no `<filter>` (the glow is
a layered halo, which the spec specifies anyway), and **no `data-*` attributes** — the allowlist
extraction cannot see `ALLOW_DATA_ATTR`, so it could not vouch for them.
`test_flow_panels.py::test_panels_emit_nothing_dompurify_would_strip` guards all four panel states
against the allowlist read out of the shipped bundle (verified to have teeth: it correctly reports
`dominant-baseline` and `vector-effect` as stripped).

**A `[flow]` theme section** (15 hexes) follows the `[console]`/`[macro]` page-scoped precedent — not
in Settings → Appearance, same reason. `call`/`put` are pinned to `gamma.POS_COLOR`/`NEG_COLOR`: the
panels sit behind the same subtab strip as the heatmap, so a cyan meaning "call" on one tab and
something else on the next would be worse than no colour coding. Rajdhani + IBM Plex Mono are already
loaded app-wide, so the section carries no font URL.

**The DOLLARS / SKEW % toggle is a REAL control inside the fragment.** DOMPurify strips inline `on*`
handlers, so the click cannot ride the markup — it is bound by the same script channel as the scrub
and reaches Python through NiceGUI's global **`emitEvent` / `ui.on`** pair. `toggle_js` is emitted on
every paint independently of the scrub payload, because the toggle exists in the empty state too
(tying it to the payload would leave a session with nothing collected yet unable to change scale
until data arrived). The old `Scale` select is **kept but HIDDEN** as the state holder: every reader
already goes through `np_mode_sel.value` and the persist+repaint path hangs off its
`on_value_change`, so the toggle writes through it and one code path still owns the change — two
VISIBLE controls for one setting would be the real problem. **The click payload is untrusted** (it is
persisted to settings.json and read back on the next page build), so `normalize_mode` guards it on
both sides and the handler unwraps the bare-string / one-element-list shapes `emitEvent` can produce.
Labelled SKEW %, not the spec's "PERCENTILE", because that is what the mode computes — a signed share
of session premium, bounded ±100% (verified over 20k random rows) — and what the rest of the app
calls it. The footer line, the y-axis labels and the leaderboard/chip units all follow the mode;
without the suffix a skew axis and a dollar axis are indistinguishable at a glance.

**Kept deliberately:** the 28-symbol Net Prem selector, its group tabs and persisted selection (the
spec's 7 symbols are its sample data); and `net_prem_status_text`, which reports a failure mode
nothing else can see (a stale publish INSIDE the collection window) and is clock-driven for that
reason.

**Removed:** `flow_figure`, `net_prem_figure` and the `FLOW_PRICE/CALL/PUT` palette, plus the 12
tests that only exercised them. Four tests whose invariants outlive the chart were **rewritten**
against the underlying readers (`net_prem_color` stability, `_np_rows` ts sorting, mode-aware
`net_prem_missing`, `_np_selected` junk guarding). The `chart_kind` registry gained an explicit
`_PANEL_VIEWS` set so its completeness guard still FAILS on a new unregistered view.

**Verified in a browser** against a standalone harness (real `ui.html` → real DOMPurify), because
what can go wrong here is invisible server-side: both ribbon tones render, terminus declutter lands
at exactly `min_gap`, the leaderboard reorders on scrub, nothing overflows either viewBox, and the
cursor dot lands on its line to **0.00px** — measured against real SVG path geometry via
`getPointAtLength`, not against the payload that placed it. The scale toggle was verified to
round-trip **to Python** (a server-side click counter incremented, so the DOM repaint alone could not
have produced it), in both directions, with the scrub still binding correctly after two full fragment
swaps. Screenshots time out in this environment (the documented pane caveat), so verification was
DOM-measured throughout.

**Suites:** webgui **1564 green**; options_svc **1091 green**; options-scanner **1439 passed / 11
failed / 2 skipped**, the failing SET byte-identical to the documented baseline (compare the set,
never the count). ⚠ **`options_svc/tests/test_flow_alert_window.py::test_gth_signal_still_fires_at_the_open`
is FLAKY** — it failed once in a full run, then passed in isolation and in two subsequent full runs.
Unrelated to this work (nothing here touches flow alerts); noted so the next person does not spend
the time attributing it twice.
**Restart `options_svc` AND the webgui** — the collector must start writing the `prem` view before
the ladder has anything to show.)

**Prior —** 2026-08-15 (**Market Dashboard visual redesign — the "Macro Board"** — a
presentation-only redesign of `/market` from an approved spec + reference prototype. Data,
grouping, categories and the ~2 s cadence are UNCHANGED; this is skin + motion only.

**Design principle: spend the intensity budget only on tiles that changed.** ~90 tiles on a 2 s
cycle, so the flat majority stays recessed (dimmed, no accent) and colour/glow/motion are reserved
for movers. Page-scoped exactly like the Market Regime Console: a `[macro]` section in
`config/theme.toml` (+ `theme.py` `DEFAULTS["macro"]`, `macro_colors`/`build_macro_tokens`/
`build_macro_css`/`build_macro_font_head_html`, exported as `MACRO_*`), and ONE `ui.add_css` block
carrying exactly the four house-approved un-expressibles — clip-path notches, the flash keyframes,
the radial page ground, and the per-tile custom-prop washes. Everything else is Tailwind.

**What shipped:** notched (clip-path) category panels with a per-category left **accent bar**
(`[--mb-acc:#hex]`), near-black tiles with a **magnitude-scaled wash** (Skin A) quantised to a
finite `[--wash:rgba(...)]` palette; a **top rail** — Chakra Petch wordmark, pulsing live dot,
`HH:MM:SS` clock (a 1 s `ui.timer`), a **breadth meter** (advancing vs declining across every tile,
sheared split bar) and an A/B **skin toggle**; **flash-on-change** — an ignition bar sweep + price
flare (`mbig`/`mbpx` keyframes) fired ONLY on tiles whose displayed value actually moved
(server-side `tile_signature` diff → one batched `ui.run_javascript` reflow-retrigger), so a frozen
feed produces zero flashes (verified) and a live change flashes just that tile (verified by injecting
one changed value → exactly that tile flashed, not the board). **Skin B (Heat Lattice)** — a second
skin (continuous heat fill, no panel chrome, bloom-on-change) toggled + **persisted** via
`app_settings.macro_skin` (default "A"). Three faces load page-scoped (Chakra Petch / Rajdhani / IBM
Plex Mono).

**Three spec open-questions resolved (with the user):** the tile's third line = **skew where present
(SPX/NDX/broad-ETFs/Top-10/BIG10), else the description** (uppercased, truncated) — preserves the
option-skew line the spec requires; **sparklines dropped** (no Tier-1 data source — the board cache
carries no per-tile history, and fabricating it is out); **both skins** built.

**One correctness deviation from the prototype, deliberate:** tile direction (up/down/flat), flash
colour and wash colour key on the service's **polarity-aware `color_state`**, NOT raw pct sign — so
VIX-up stays red / risk-off. Only the wash MAGNITUDE scales with `|%change|`.

TDD throughout (`test_market.py` rewritten for the new pure helpers — direction/magnitude/wash/heat/
descriptor/breadth/signature; `test_theme.py`-adjacent smoke of the macro builders); webgui **1481**
green. **Live-verified in DEV (`:9500`)** — notches, custom-prop arbitraries generate in the bundled
JIT, Chakra Petch loads, Skin A↔B toggle + persistence, breadth 35/10, change→flash. Files:
`webgui/pages/market.py` (rewrite), `webgui/pages/options/theme.py` (+macro helpers),
`config/theme.toml` (`[macro]`), `webgui/app_settings.py` (`macro_skin`), `webgui/tests/test_market.py`.
**Restart the webgui.**)

---

**Last updated:** 2026-08-14 (**`/sentiment`'s top became the Market Regime Console** — a
single-screen dark console built from a supplied hi-fi handoff, now in the repo at
`docs/design/2026-08-14-market-regime-console/`. Plan + every measured spike:
[the plan](plans/2026-08-14-sentiment-console-redesign-plan.md), which is the detailed record; this
entry is the summary.

**It is an evolution of the page, not a foreign design.** The handoff's sample numbers ARE this
app's live numbers (trend day 68 vs live 67.8, month 83 vs 82.7, the regime shares, "leads by
10.2 pp"), and its Signals cells reuse this page's own descriptors verbatim. An audit of ~20 data
points found only **three** gaps, all numbers the engine already computed and then formatted away.

**Tier 2 (additive, needs a `sentiment_svc` restart):** `derived.velocity.values` (the ROC/z numbers
behind the display string), `derived.divergence_detail` (the component pair, gated on the engine's
own string so the two cannot disagree), and `regime.evidence_detail` — `score_regimes` already knew
which regime produced each evidence line and was discarding it. Severity follows the **source
regime**, and the live classifier validated that rule rather than fitting it: all six real evidence
lines split exactly as the designer hand-coloured them.

**Tier 1:** `[console]` in `config/theme.toml` (hex-only; alphas live in the token layer),
`console.py` primitives, `console_dial.py`, `console_cards.py`, `console_regime.py`,
`console_page.py`.

**Everything was measured, not assumed.** A spike probed every class shape in the running app —
glows, `radial-`/`linear-`/`repeating-linear-gradient`, arbitrary opacity, arbitrary grids, and a
full font stack all generate, so the whole design is expressible under the Tailwind-first rule with
no CSS beyond the `pulseDot` keyframes. Two stale comments were corrected as measured-false
(box-shadow arbitraries accept a hex; only `var()` is the JIT trap), and
`document.fonts.check()` was caught reporting a font present while it was demonstrably not loaded.

**Three defects found by building it:** the dial's 100% case drew NOTHING (a 360° sweep's endpoints
coincide, so SVG renders an empty path — now a `<circle>`); the confidence meter rounded halves to
even, so its midpoint flipped on parity; and the handoff's own bipolar scale contradicts its stated
widths (self-consistent at 2.5, not 5).

**Deviations taken deliberately:** Signals cells are coloured by tone rather than by cell position
(the handoff's fixed hues would paint today's Neutral reading in Long/Bullish colours); the 2×2
matrix is restored because the 1×4 stack existed only to fill space beside the now-removed rings;
and the layout is fluid-capped at 1440 rather than fixed.

**Known limit:** the console needs ~1084px of content width; below that the page scrolls
horizontally. It was already desktop-only. webgui **1479 green**.)

**Prior — 2026-08-14** (**the Market Regime panel stopped spending all its ink on the part
that never changes.** The `/sentiment` membership mix was a percent-stacked area chart; it is now
a **ranked panel** — `webgui/pages/regime_mix.py:regime_mix_svg`, a pure SVG string mounted with
`ui.html` and updated via `el.content`, the same idiom `rings.py` established the same day.

**Why, measured rather than asserted.** Read against the live session (2026-08-14, 78 samples):
the widest membership swing all day was **9pp** (Balanced), Trending moved 2pp, and **Breakout sat
at exactly 0.000 from open to close** while still holding a fifth of the legend. Percent-stacking
then *guarantees* the five bands fill the height, so the chart's whole area encoded the static
part — and the two things that actually happened were invisible in it:

* the lead **changed hands** at 09:31, out of a **0.2pp** gap at the open — a genuine coin-flip
  rendered as a flat seam;
* **Stressed rose from literally zero to 7.5pp** by 12:50 before easing to 4.9pp — the day's real
  story, drawn as a sliver at the top of the stack.

**What replaced it.** One row per regime, sorted by current share. The bar is scaled to **the
leader** (so a row reads as "how close is this to winning" — the contest), while each sparkline is
scaled to **its own** range (so a 2pp move is as legible as a 9pp one — the specific failure of a
shared axis on data this static). A change-since-**session-open** column, split on the same 4h gap
constant the intraday figures use, so a day boundary can never make it compare across sessions.

**The footer is a new signal, not a restatement:** the leader's margin over the runner-up, plus
the session's tightest. `unclear` measures *evidence strength*, not how close the top two are — so
at 0.2pp the committed label was very nearly arbitrary and nothing on the page said so.

**Two deliberate trade-offs.** (1) Ranking gives up the old fixed order's stable reading position.
Kept anyway: with five rows a lead change is rare and is the most interesting event of the day, so
the ORDER is signal — and ties break on `REGIME_ORDER`, so identical data can never jitter between
repaints. (2) A dead-flat series draws a **dashed rule and an em-dash**, never an auto-scaled line:
scaling Breakout to its own range would amplify floating-point dust into a plausible squiggle.

**Two things that cost time and are worth knowing.** A viewBox scales the **text** too — uncapped
at the full ~1100px content width a 13px label rendered at ~22px and dwarfed the page, hence
`max-w-[720px]` (measured back at 14.6px against the page's own 14px subtitle). And the panel
inherits `rings.py`'s sanitizer constraint: no `<style>`, no `<filter>`, `dy` not
`dominant-baseline` — `test_regime_mix.py` mirrors the DOMPurify allowlist guard, because a
stripped attribute changes nothing server-side and the page still renders, just wrong.

`REGIME_ORDER`/`_LABELS`/`_COLORS` moved into the new module and are re-exported from `sentiment`
for its headline helpers. Net **−98 lines** in `sentiment.py`. webgui **1364 green** (31 new);
live-verified in dev against prod's real session — Whipsaw 38.8% leading Balanced 28.6% by 10.2pp,
Stressed +4.9pp, Breakout dashed. **Known limit:** at phone width the SVG scales down to
unreadable — the page was already desktop-only (its two intraday Highcharts render at a fixed
1105px and overflow a 375px viewport regardless), and this panel is the only element there that
scales at all.)

**Prior — 2026-08-14** (**`/sentiment`'s four semicircular gauges became two concentric
Day/Week/Month rings — and hunting the "no data" case turned up six instances of one defect.**
Four Highcharts gauges could show two horizons between them; two SVG rings show six readings in
less space. But the substantive reason is the one a needle structurally cannot do: **say
nothing**. Six horizons need a way to render "not published yet" / "the fetch failed" that is not
a number, and a needle must always point somewhere.

**Tier 1 — `webgui/pages/rings.py` (NEW).** `ring_svg(arcs, uid, size=280)` is a pure SVG-string
builder, mounted with `ui.html` and updated via `el.content`. Chosen over a Highcharts
`solidgauge` and over a CSS conic-gradient: **rounded arc caps are impossible in CSS**, and a
plain string sidesteps both documented `ui.highchart` hazards at once — the ESM-import-map trap
and the `chart.update()` merge/stock-module minefield. Precedent: `pages/options/svg.py`.
Geometry: **270° sweep, start 225° / end 135°, clockwise from 12 o'clock** — 0 lower-left, 50
top, 100 lower-right, with a 90° gap at the bottom that the Week/Month legend occupies. Radii
112/90/68, stroke 13, ticks r=132, fixed `viewBox="0 0 280 280"` (`size` sets only width/height,
so the dial scales itself). Per-arc colour comes from **that arc's own value** via
`gauge._ramp_color`, so `config/theme.toml [gauge]` still drives the palette. The glow is a
**layered halo** — a wide translucent copy of the path under a bright one — deliberately not an
SVG `<filter>`, which the sanitizer would not pass. **`uid` is REQUIRED**: two rings share the
page and a duplicate DOM id makes them collide. **`gauge.py` is untouched** and still serves the
options detail-panel speedometer, so the app now carries **two gauge idioms** — needle for one
value in a panel, ring for multi-horizon. New page builders `sentiment_avg_or_none` /
`sentiment_avg` (`WEEK_SNAPS = 5` — the backfill is one row per COMPLETED session, so a week is
5, not 7), `sentiment_arcs`, `trend_arcs`, `_composite_arc_value`, `_trend_arc_value`;
`sentiment_30d_avg` DELETED (its only caller was a removed gauge).

**Tier 2 — the Week arc's structural read.** `compute_7d_trend` mirrors `compute_30d_trend`
(which is **misnamed** — a monthly-HORIZON structural read, not "30 days ago" and not an
average), scored on sector `week_pct` with `_CYC_DEF_SCALE_7D = 1.5` and its own
`TREND_7D_TTL_SEC = 1800`; the shared body was extracted as `_structural_trend` /
`_neutral_structural_trend`. **The Week arc costs ZERO extra Schwab calls**: `_fetch_sector_pcts`
is ONE TTL-cached fan-out serving both horizons, because `week_pct` already came off the same
`_fetch_closes` call — worth being deliberate about on a stack audited at ~68–76k calls/day.
`derive_composite_extras` gained `trend_7d` (LAST, so the positional call shape is unaffected),
`handlers._TREND` gained the slot, and it publishes on the existing 15-min `trend_due` gate;
the two structural horizons carry no hysteresis of their own and are simply HELD across gated
refreshes. **KNOWN LIMITATION:** Week and Month share the SAME daily price sub-score —
`calculate_ema_alignment`'s EMA periods are fixed, so a shorter frame changes nothing — so the
two arcs track each other and diverge mainly on sector rotation. A genuinely weekly price read
needs weekly-resampled SPY bars; deferred.

**THE RECURRING DEFECT — six instances of one failure: a missing or garbage input rendering as a
CONFIDENT reading.** A non-finite composite becoming a full 100 arc (`min(100.0, nan)` is
`100.0`, and these payloads cross Redis as JSON, which both emits and accepts
`NaN`/`Infinity`, so a service-side divide-by-zero round-trips intact); an unparseable score
becoming a maximally-BEARISH 0 via `_safe_float`'s 0.0 default (the mirror of the same bug, one
input to the left — `'n/a'` gave DAY 0.0 beside WEEK/MONTH None, off the same row); a NaN sector
pct becoming **maximum cyclical leadership at full confidence** — measured,
`score_sector_participation(5, 11, nan)` returns `TrendSub(67.27, confidence=1.0)`, because
`intraday_trend._clamp` is `max(lo, min(hi, v))` and that yields the HIGH bound for NaN (fixed by
`_finite_pcts`, which DROPS non-finite moves so the missing sector lowers `n_total` and with it
the confidence, which is what a missing sector should do — pre-existing, but the Week arc would
have doubled the exposure); and **the one that actually fires in production** —
`compute_7d_trend`/`compute_30d_trend` swallowing their own exceptions to return a fully shaped
**`score 50.0 / confidence 0.0`** dict, so on any proxy blip a good reading is replaced by a
confident-looking neutral 50 and **every absent-key guard misses it**. That is why
`_trend_arc_value` keys on **confidence**, not key presence — verified sound, not assumed:
`blend_trend` weights each sub-score by its own confidence, so the aggregate rounds to 0.0 only
when there was no usable evidence at all, while a genuinely neutral 50/50 read at full confidence
scores agg 0.65 and passes straight through. **A needle cannot express "no data"; the ring's
`None` → track-only + em-dash can, and that is the strongest single argument for the redesign.**
⚠ **NOT fixed — the PRICE sub-score carries the same NaN exposure as the sector one.** Measured:
an all-NaN structural price read (`macd_hist`/`rsi`/`adx`, `compute.py:1248-1253`) scores
**82.50 — near-maximum bullish — at UNCHANGED confidence 0.333**, where a sane read scores 56.25;
the same all-NaN read in `compute_intraday_trend`, **the live Day gauge** (`compute.py:439-443`),
scores **92.50**. Deliberately deferred to its own task: the fix must cover both call sites with
one shared filter.

**The Signals column, rebuilt.** The two ring columns measure ~460/476px against a 232px 2×2 tile
grid — ~225px of void. Now a **1×4 vertical stack** of glowing tiles at 487px, column widened
210→300px; each tile is icon + letter-spaced label / big value with a neon `text-shadow` /
hairline rule + dot / footer icon + descriptor. **`velocity` and `divergence` are rendered
again** — the service had been computing and publishing both **every cycle with no renderer at
all** since the intraday graphs replaced the old text block, a silent regression rather than a
decision. **`_word_tone`:** BIAS and SIGNAL carry `live_composite.signal_band`'s OWN vocabularies
(`Long/Neutral/Cautious/Short` and `Strong Bull…Strong Bear`), NOT the composite's `bias` field,
and `bias_color` only substring-matches bull/bear — so "Long" and "Short" read amber. Each tile
now colours from its own word and **`bias_text_class` delegates to `_word_tone`**, so the
headline under the ring can no longer contradict the tile beside it (it was rendering "7.28 ·
Long" in amber above a green "Long"). Styling stays Tailwind-first with **no `ui.add_css`**;
`theme.TILE_3D` is deliberately flat ("NO bevel or drop shadow") and was NOT redefined, so the
glow tokens are local to `sentiment.py` — flagged in CLAUDE.md that the page is therefore mixed,
and that a third glowing element would mean the theme has moved in practice.

**Traps worth keeping — all now in CLAUDE.md.** (1) **`ui.html` sanitizes through the BUNDLED
DOMPurify, and its allow-list is READABLE.** `html.js` calls `setHTML`, but that is not the
native API: NiceGUI monkeypatches it at `templates/index.html:144` to
`this.innerHTML = DOMPurify.sanitize(html)` (its comment: native `setHTML` strips class
attributes). **`dominant-baseline` is not allow-listed** (`alignment-baseline` and
`baseline-shift` are), so every ring label silently dropped to the alphabetic baseline **on the
client** while the server string stayed correct and the whole suite stayed green. Fixed with
`dy="0.35em"`; `sanitize=False` was considered and rejected as disproportionate. `test_rings.py`
now pins the **general property** — that `ring_svg` emits nothing DOMPurify would strip — by
extracting the allow-list from the shipped `dompurify.mjs` and dropping any run containing
`script` (DOMPurify ships DENY lists too, and unioning those in blessed `<use>`). (2)
**CLAUDE.md's Tailwind JIT warning was overstated and is corrected in place**: probed live,
`rgba()` inside a `shadow-[…]` arbitrary DOES generate (the Refresh button's shadow is a live
example), as do `bg-gradient-to-b from-[#hex] to-[#hex]`, `[text-shadow:…]` and
`drop-shadow-[…]`. The real limitation is **`var(...)`**. (3) **`getBBox()` on an SVG `<text>`
returns the EM box, not the ink** — it reported the centre block 4.2px low where the true figure
was **10.7px**, because an em box carries ascender/descender space digits and all-caps captions
never fill; measure with canvas `actualBoundingBoxAscent`/`Descent`. (4) **A test suite can
silently transact against the live proxy** — adding `compute_7d_trend` to the handlers without
stubbing it in `_patch_compute` left **11 tests opening real connections to `127.0.0.1:8100`**
while reporting green, and dev borrows prod's proxy. (5) **Renaming an autouse pytest fixture
does not disable it** — the decorator still registers it, so a rename-based "disable" produces a
false all-clear when you are verifying a leak fix; neuter the body instead. (6) **`git commit
--amend` takes the WHOLE index**, so staging by explicit path does not protect against this
repo's concurrent-session hazard and can orphan the other session's commit.

**Test isolation the change forced:** an autouse `conftest` fixture now resets
`_SECTOR_PCTS_CACHE` / `_TREND_7D_CACHE` / `_TREND_30D_CACHE` before AND after every
sentiment_svc test — without it, any test stubbing `_fetch_closes` leaves its fixture values in
those module globals and a later un-monkeypatched self-fetching call silently consumes them (a
probe `compute_30d_trend()` scored its sector sub-score 73.33 off stale stub data with ZERO
fan-outs). The suite only stayed green because `pytest-randomly` isn't installed and the ordering
happened to be kind. **Restart `sentiment_svc` + the webgui.** **webgui 1336 passed / 0 failed**
(was 1253 at branch point — the "1190" figure in CLAUDE.md was stale and is corrected);
**sentiment_svc 279 passed / 1 failed**, the documented pre-existing
`test_compute_regime.py::test_daily_history_wins_over_session_latch`. 25 commits, merged into
`Using_Highcharts`. Design/plan:
[design](plans/2026-08-14-sentiment-trend-ring-graphics-design.md) /
[plan](plans/2026-08-14-sentiment-trend-ring-graphics-plan.md).)

**Prior — 2026-08-14** (**Market Regime renamed + given a direction axis.** The five display
names mixed three vocabularies — three tape-behaviour nouns, one *strategy* (Mean Reversion) and one
market condition (Volatile) — and one of them misdescribed its own evidence. `mean_reversion`'s five
inputs (low ADX, flat EMA, mid-band width, balanced profile, above the gamma flip) all say price is
**AT** the mean; **nothing** in the evidence set measures distance from a mean or an extreme, so
"Mean Reversion" promised a fade the model never tested. Renamed **display-only** (internal keys are
the contract + DB columns + driver packet, so they are untouched): **Balanced** / Trending / Breakout
/ **Whipsaw** / **Stressed**. Whipsaw vs Balanced now carries the distinction that actually separates
them (energy: high ATR + low ADX vs quiet balance); "Stressed" replaces "Volatile" because
`VIX_STRESS_LO` is 22 and the attack fires near VIX 30 — stress, not crisis — and because breakout and
whipsaw days are volatile too.

**Direction.** `trending`/`breakout` now read **Rallying / Firming / Retreating / Softening /
Breakdown**. The sign was already computed and thrown away — `regime_evidence._ema_slope_atr` returns a
SIGNED slope that `_trending` discarded via `abs()`. The intensity math stays sign-blind (a trend day
is a trend day either way), so this is a label adornment on the same five-member simplex, **not** a
sixth regime — splitting `trending` would need a DB column, a chart series and a contract change, and
would tear the membership across two bins whenever the slope flips mid-session.

**The contradiction risk was the real design problem.** Two independent direction reads exist (the
regime's 5-min SPY slope vs the 15-min Market Trend composite over price+breadth+sector+VIX), and they
diverge on a genuine condition — index up on narrow leadership with negative breadth. A word derived
from either alone could contradict the other panel. `market_regime.direction_sign` therefore names a
direction **only when both agree past their deadbands**, otherwise rendering the neutral base label —
which is exactly the previous behaviour, so the neutral word is a floor, never a regression.
`handlers._committed_trend_score()` reads the SAME `smoothed_score` the gauge renders, taken BEFORE
`_REGIME_LOCK` so `_TREND_LOCK` is never nested inside it. `commit_direction` is asymmetric on purpose:
**two** consecutive reads to claim a direction, **one** to drop back to neutral.

**Rendering.** The stacked-area series names stay the BASE words (the fixed order + stable names ARE
the reading position); direction adorns the headline + transition line only. The headline colour now
follows the direction for the two directional regimes — the fixed green was painting a down-trend as
though it were bullish. The page re-derives the label from `(committed_label, direction)` instead of
echoing the payload's `label`, so a held sample can't outlive a rename, with `unclear` short-circuiting
to "Unclear". The pushed market snapshot also stopped rendering RAW KEYS ("mean_reversion → trending")
in its transition line. Additive `direction`/`direction_strong` on `RegimeState` + `_REGIME_PUBLIC_KEYS`
(a new test pins that allowlist against the test-side duplicate). Words are duplicated across four
tiers by necessity — `scoring/market_regime.REGIME_DISPLAY` is the source, webgui/driver_svc/
options_svc mirror it, since none may import that package. **Restart `sentiment_svc` + `options_svc` +
`driver_svc` + the webgui.** sentiment-dashboard **484** / webgui **1253** / options_svc + driver_svc +
sentiment_svc + contracts **1618** green, against the documented baselines.)

**Prior — 2026-08-13** (**`big_delta` PUSH go-live via a SEPARATE push bar + two tuning aids**
(commit `6b1636c`, live in prod). The detector's single `rel_threshold` gated BOTH the screen and the
phone push, so enabling push at 20% would be ~100 Telegram alerts/day. Fix: a distinct
**`[big_delta].push_threshold`** (0.35) — the detector still FIRES to the **Flow screen at
rel_threshold 20%** (comprehensive, ~100/day) but only fires whose share of gross **≥ push_threshold**
earn a phone push (**~15–25/day**; 23 of 101 on 08/13). `flow_alerts.big_delta_should_push(alert, cfg)`
+ the `handlers.run_flow_alerts` gate replace the old blunt push flag; `push=true`. **Routing** (prod's
gitignored `notifications.json`): big_delta → the **global Telegram chat** (the sibling flow types
don't override it, so it lands with them) + a **dedicated Discord webhook** in `routes.big_delta.discord`
— `flow_category` passes "big_delta" through as its own route key, so **no code change** was needed for
routing; webhook delivery **live-verified (HTTP 204)**. **Two tuning aids shipped with it:** **#1** a
**"live fires by rel_threshold"** table baked into the daily 15:30 `flow_delta_instrumentation` report
(flags the FIRE + PUSH bars; live-verified reproducing 08/13's 20→101 / 25→49 / 30→34 / 35→23 / 40→15),
and **#2** a sortable **Share %** column on the Flow Alerts screen (`flow._share_pct` stamps the numeric
share so Quasar ranks by conviction — live-verified renders + numeric sort). Pushes fire only in RTH →
first live pushes are the next session. Design decision (via AskUserQuestion): the separate push bar
over a single-threshold-at-35% (keeps the comprehensive screen), built on the two-session finding that
the 20% total is stable ~100/day while higher-threshold counts swing ~2× (25%: 25→49). Tests:
flow_alerts +4 (push gate), handlers 2 rewritten, instrumentation +4, flow_page +2 & column test
updated — **options_svc 1071 passed / 1 PRE-EXISTING baseline fail** (`captured_autoclose` full-suite
pollution, confirmed on clean `1eeba6b`, flagged as a separate task), **webgui 1248 green**. Pushed to
GitHub.)

Prior — 2026-08-13 — *work done 2026-08-02, merged to main 08-13* (**Options extended trading hours (Cboe GTH + Curb), effective 2026-08-17
— plus the market-calendar consolidation it forced**. Cboe C1 adds a morning **GTH** session
(07:30–09:25 ET = **06:30–08:25 CT**) and an afternoon **Curb** session (16:00–16:15 ET = **15:00–15:15
CT**) for select multi-listed **single-stock** options. Confirmed date + spec: Cboe notice
[C2026061202](https://www.cboe.com/notices/content/?id=60500), which records **2026-08-17 as a DELAY from
2026-07-13** pending regulatory approval, and the
[Equity Options ETH FAQ](https://www.cboe.com/document/tech-spec/document/technical-specifications/equity-options-extended-trading-hours-faq).
**Use Cboe's vocabulary — GTH and Curb — NOT "pre-market"/"after-hours"**; Cboe explicitly distinguishes
option GTH from the equity pre-market, and GTH is also the name of the index options' 20:15–09:25 ET
session (**out of scope here** — this covers only the equity GTH window).
**⭐ The design's keystone: Schwab already publishes eligibility.** The option-chain response carries a
root-level **`ethOptionEligible`** boolean. Live-probed: `NVDA/TSLA/AAPL/PLTR/AMD/AVGO/MU` **True**,
`SPY/QQQ/IWM/KO/XOM` **False**, `$SPX/$VIX` **True** — and all seven True names appear in Cboe's published
21-symbol launch list while none of the False ones do. **`MU` is the decisive case**: press coverage
described the set as "Mag 7 plus PLTR, AVGO, AMD", which excludes it — a hardcoded list would have been
wrong on day one. Cboe re-balances the list **twice a year** and may grow it to a **100-class cap**, so
**never hardcode it**; it is read from a chain the 1-min GEX poll already fetches (**zero extra API
calls**), harvested on the existing `on_chain` hook into **`cache:options:eth_eligible`** via the new PURE
`services/options_svc/eth.py`. Caveat: Cboe's reference data has **separate GTH-Eligible and Curb-Eligible
columns** while Schwab exposes one boolean — identical for the launch set, latent otherwise.
**Scope decisions (owner-approved):** cash-adjacent sessions only (**no overnight index GTH** — GEX
collection is already ~36k of ~70k daily Schwab calls); **observe-only posture** — the execution layer
stays inert in ETH, which was ALREADY the status quo and needed no change; and **everything gated on
2026-08-17**, held in config so a further slip is a one-line edit.
**What actually changes on 2026-08-17:** GEX collection starts **06:30 instead of 08:00 for eligible
symbols only** (~7 of 45). Polling the full universe would cost ~4,050 calls/day for ~38 names that
aren't quoting; the eligible subset costs **~630/day ≈ +0.9%**. Cold start with no cached eligibility
**skips** the GTH poll — never guesses. **The Curb session changes nothing** (15:00–15:15 CT was already
inside the 15:20 stop).
**⚠ The bug this nearly shipped with — flow alerts.** `collect_gex_history` runs `run_flow_alerts` after
EVERY collection tick, so from 08-17 detection would have started at 06:30 on thin GTH prints. Worse,
`run_flow_alerts` marks the day-scoped cooldown/seen-set **at DETECTION** (`handlers.py` UOA loop;
`flow_alerts.detect_flow_alerts` mutates the map in place). And contrary to a first reading,
**`send_flow_alert` has NO market-hours gate** — the `market_hours_only`/`_in_market_hours` check lives in
`notify_signals` (scanner/captured signals), a different path. So unfixed this meant **real 06:30 phone
pushes**; and gating only the PUSH would have marked the signal seen and **destroyed it — it would never
fire, not at 06:30 nor at the open**. Fixed by gating **DETECTION** on
**`mc.in_collection_window(now)`** (08:00–15:20 CT — precisely the window `run_flow_alerts` is reachable
in today, so **provably inert before activation** while closing exactly the new 90-minute GTH stretch).
`[alerts].fire_in_extended_hours` (default **false**) opts back in. `publish_flow_skew` is gated the same
way: `latest_skew_by_symbol` has **no date filter**, so during GTH it would emit `$SPX` fresh off thin
prints beside `SPY`/`QQQ` frozen at yesterday's close as one "current" snapshot, and the consumer ignores
`ts`.
**NEW infrastructure — `shared/market_calendar.py` + `config/sessions.toml`.** Mapping the change exposed
**10 duplicated NYSE holiday sets and 14 hardcoded window constants**. Rather than add a 15th, all of it
consolidated onto one module first, as a **behavior-preserving refactor** (the never-shipped
[2026-07-03 config-consolidation plan](docs/plans/2026-07-03-config-consolidation.md), finished and
extended). It **absorbed `sentiment-dashboard/market_calendar.py`** (now DELETED — same module name, same
three function names, but **inclusive** `prev/next_trading_day` vs our **exclusive**, an invisible
one-day trap) and **adopted its algorithmic derivation** (Easter / nth-weekday / observed rules), so
**there is no yearly holiday edit any more**. Public API: `nyse_holidays(year)` (lru_cached, valid
2022-on, does NOT model ad-hoc closures), `is_holiday`, `is_trading_day`, `prev/next_trading_day`
(**exclusive**), `Session` (`GTH`/`REGULAR`/`CURB`/`CLOSED`), `session_at`, `is_regular_hours`,
`is_extended_hours`, `extended_hours_active`, `in_window(name, now)`, `window_bounds`,
`in_collection_window(now, *, eth_eligible=False)`, `session_flip_time`, `alerts_fire_in_extended_hours`.
**API gotchas worth knowing:** windows evaluate in **their own timezone** (`driver_entry` is **ET**);
`in_window` **raises `KeyError`** on an unknown name (a typo is a code error, deliberately not degraded);
`window_bounds` accepts `end` **or** `stop`; **`in_window` is inclusive both ends but
`in_collection_window`'s stop is EXCLUSIVE** (each mirrors the legacy predicate it replaced — **do not
unify**); **REGULAR wins the 15:00 overlap** with Curb; naive datetimes are treated as **CT**; a malformed
`sessions.toml` degrades to defaults for bad **values AND bad shapes**, and `load_config()` returns the
**cached** dict (don't mutate).
**The equivalence gate paid for itself.** `shared/tests/test_market_calendar_equivalence.py` proves the
shared helpers reproduce every legacy predicate **minute-by-minute over four representative days** —
and caught a divergence nobody anticipated: `driver_svc`'s `hm >= RTH_END` makes its entry window's end
**EXCLUSIVE**, so a straight migration would have opened a **16th checkpoint slot at 15:30 ET**, firing a
Claude call and a possible entry inside the "no new entries in the last 30 min" zone. Closed
declaratively via **`end_exclusive = true`** on that window (defaulted in `_DEFAULTS` too, so a corrupt
TOML degrades to the SAFE behavior). **One divergence remains deliberately:** `sentiment_svc`'s RTH
excludes the 15:00:00 instant while `is_regular_hours` includes it — pinned by three tests so its
migration had to preserve exclusivity consciously.
**THREE REAL BUGS the consolidation surfaced, each fixed in its own labelled behavior-change commit AFTER
the refactor landed** (so the refactor kept its identical-output guarantee): (1)
**`options-scanner/scanner_engine.py` `HOLIDAYS_2026`** was a **tenth** site holding **9 dates, 2026 only
— missing Juneteenth and ALL of 2027** — and it feeds `paper_engine.in_trading_window`, so **from
2027-01-01 the paper engine would have run entry/manage cycles on every market holiday**; (2)
**year-boundary spill** — `is_holiday` consulted only `nyse_holidays(d.year)`, so `2027-12-31` (observed,
because 1 Jan 2028 is a Saturday) read as a **trading day**; now also checks `d.year + 1`; (3) the
**bounded `HOLIDAYS` alias** (2026-27 union) disagreed with the year-general predicates from 2028 — e.g.
`active_session_date` would return MLK 2028-01-17 as its own session — so it was **RETIRED entirely**;
`em_cone`'s `holidays=` param was **redefined** as "extra ad-hoc closures" over the derived calendar
rather than being handed a bounded set.
**FOUR tests were found ENCODING bugs** rather than catching them (a `driver_entry` boundary pinned at
the wrong value, one pinning the bounded alias, one pinning the pre-fix legacy sweep, and
`test_em_cone_skips_weekends` anchoring on Juneteenth while calling it "Friday"). Worth expecting more.
**UI (display only):** an **`ETH` badge** on the Opportunity Board (`matrix.build_rows` gained an additive
`eth_eligible`; at 07:00 the ~38 frozen rows must read *not eligible*, never *stale*) and the collector
status strip now **names the session** (`GTH`/`Regular`/`Curb`/`Closed`). **Hazard H7:** a class opens GTH
only once its underlying prints, so no data during GTH is **normal** — `classify_collector_status` returns
*"awaiting GTH opens"*, but **only before 08:00**; from 08:00 the full universe is polled so a growing age
is a real fault again. `gex_status`'s `MARKET_OPEN` (which had said **8:30 since collection moved to 8:00
in July**) now derives from `window_bounds("collection")`.
**Settlement deliberately UNCHANGED, and now verified rather than assumed:** eligible names DO trade the
Curb on expiration day, but per the Cboe FAQ **OCC strikes settlement and the ITM determination on the
16:00 ET NBBO** — so `paper_engine.SETTLE_HOUR_CT = 15` and `options_calculator.EXPIRY_CLOSE_HOUR_ET = 16`
are correct. Pinned by a guard test quoting the FAQ. Likewise `paper_engine`'s 08:30–15:00 window and the
driver's 09:45–15:30 ET entry window are **pinned inert in ETH** with a verified tripwire.
**STILL OPEN — needs a live session on 2026-08-17 (~07:00 CT):** (a) does Schwab serve **fresh option
quotes** from 07:30 ET (check `quoteTimeInLong`; if it reads the prior close, set `extended_hours_from`
to a future date and the feature reverts to inert), and (b) does **`totalVolume` accrue GTH prints**?
Cboe marks them not-last-sale-eligible with an Extended Hours `"v"` condition and they do **not** count
toward the daily high/low, so `last` will be stale — every engine path here computes from **`mark`**,
which stays live. **Record a contract's `totalVolume` at 15:20 CT on Fri 2026-08-14** so Monday's
comparison is possible. **Restart `options_svc` + the webgui.** Green (all measured 2026-08-02): options_svc
**915**/2-baseline, webgui **894**, driver_svc **221**, sentiment_svc **188**/1-pre-existing, market_svc
**49**, portfolio_svc **32**, trade_svc **69**, shared/tests **80**, notify **56**, contracts **46**, bus
**24**, proxy **98**, options-scanner **1311**/17-flaky; ruff clean. Design/plan:
[design](docs/plans/2026-08-02-options-extended-hours-design.md) /
[plan](docs/plans/2026-08-02-options-extended-hours-plan.md).

**Prior — 2026-08-11** (**`big_delta` — a fourth flow detector on directional EXPOSURE,
shipped quiet-live.** The three live flow detectors (crossover / unusual-activity / gamma-flip) all
measure DOLLARS, not directional exposure — a one-off SPY measurement found **$12.71B of
delta-notional in cheap OTM contracts that fell below the $5M premium floor**, invisible today. The
daily **15:30 `FlowDeltaInstrumentation`** post-close run (`tools/flow_delta_instrumentation.py`)
calibrated the threshold over **three sessions** (Fri 08-07 / Mon / Tue): a **relative** trigger (a
contract's share of its symbol's OWN gross delta-notional) beats an absolute $ floor — the absolute
floor makes **$SPX the top symbol at every level, every session** (repeating the mega-cap bias the
flat $5M premium floor has), while **~20% relative is stable at ~46–53 alerts across ~35–45
symbols**, ~46 genuinely new. (Two findings recorded: the modelled baseline **overcounts real fires
~2.4×** — closing delta ≠ intraday — and the "$SPX fires zero UOA" blind spot was a **Friday-only
artifact**, 0→26→37 across the three sessions.) **The detector** `flow_alerts.detect_big_delta`: one
walk of the poll's already-fetched chain (delta was sitting unused in the contract dict), a
**`|delta|>1` sentinel guard** (Schwab's `−999`, else it fires daily on unpriced junk), a **delta
band** (0.05–0.85, drops near-zero + deep-ITM), accumulate the symbol's IN-BAND gross, then flag
contracts clearing `rel_threshold × gross` AND a `min_contract_notional` absolute floor (so a big
share of a tiny name isn't noise). Runs in the 1-min GEX `on_chain` callback beside `detect_uoa` (ONE
`load_thresholds()`, best-effort so it can never break collection), drains in `run_flow_alerts`.
**Ships QUIET-LIVE** (`[big_delta].push=false`): real alerts land on the **Flow Alerts screen** (its
own violet/fuchsia hue — unsigned exposure, not bull/bear) but do NOT chime/toast/phone-push; flipping
`push=true` in `config/flow_alerts.toml` is the one-line go-live (the two suppressions — the
`send_flow_alert` skip + the `new_flow_alerts` chime exclusion — gate on it). Fully config-driven
(`[big_delta]` block: enabled/push/rel_threshold/min_contract_notional/delta band/top_n). The **15:30
instrumentation stays running** as the tuning loop, now reading `[big_delta]` for a "Live config —
what today's config would fire" line + a big_delta reconciliation vs what the live detector actually
fired (`cache:options:flow_alerts` type-filtered), candidate ABS/REL exploration tables kept
(`--rel`/`--abs` overridable). Tuning loop: read the 15:30 report → compare live-config-modelled vs
actually-fired vs candidates → edit the toml → restart `options_svc` → flip `push=true` when the real
rate is right. A code review caught one drift — the instrumentation's `gross_by_symbol` summed over
ALL contracts while the detector's gross is IN-BAND-only, so the live-config line could miss an alert
the detector fires; fixed to band-filter when given the config (the candidate tables keep the all-rows
denominator for continuity). Design/plan:
[design](plans/2026-08-11-big-delta-flow-detector-design.md) /
[plan](plans/2026-08-11-big-delta-flow-detector.md). options_svc **975**/2-baseline, webgui
**1216**/0, instrumentation **9**. Built subagent-driven (TDD) + a final holistic review. Commits
`cd17f56`→`c17457f`.)

Prior — 2026-08-11 (**Recommender lifecycle gate — the paper + driver books had silently
stopped taking profit at +50%; plus two inert placeholders.** The 2026-08-09 captured-autoclose
feature reworked the **SHARED** `signal_recommender.recommend()` into a lifecycle: at +50% credit it
ARMS break-even and returns HOLD instead of the old TAKE_PROFIT/`TARGET_HIT`. That was scoped to
captured signals — the design explicitly kept the manual paper account and the driver's isolated
account OUT of scope ("they have their own managers") — but `recommend()` is shared, and
`paper_engine.run_manage_cycle` (which BOTH those books run through) calls it directly with a minimal
ctx and closes only on TAKE_PROFIT/CUT. So both paper books quietly **stopped banking winners at
+50%** and, never setting `be_armed`, got no break-even protection either. Live-confirmed
(`recommend({entry_credit:1.0, unrealized_pnl:60}) → HOLD`) and it had **SHIPPED to prod**
(paper-only, no real money, but it skews both books' P&L and fights the driver's press-and-bank
mandate). The full suite stayed green because `test_paper_engine` **MOCKS** `recommend()`. **Fix:**
gate the +50% arming on an explicit `ctx["lifecycle"]` opt-in — `build_mark` (the captured path)
passes `lifecycle=True` and keeps arming; the paper/driver manage cycle keeps its minimal ctx and
gets TAKE_PROFIT/`TARGET_HIT` back. Only Rule 5 is gated (a credit spread at +50% has a decayed short
delta, so "+50% AND delta-breached" is economically contradictory → identical P&L). An **UNMOCKED**
`test_manage_cycle_takes_profit_at_50pct_unmocked` now guards it (RED before, GREEN after). **Two
inert placeholders came with it** (the deferred items from the captured-autoclose design, both ship
OFF): a **peak-driven profit-lock ladder ("ratchet")** — Rule 3's break-even stop generalizes to
`max(be_level, locked_profit)` from a `(peak_frac, lock_frac)` ladder; `DEFAULT_TRAIL_LADDER=[(0.50,
0.0)]` = today's plain break-even (byte-identical), `RATCHET_TRAIL_LADDER=[(0.50,0.0),(0.65,0.25),
(0.80,0.50)]` is defined but unwired — and a **flag-gated manual-paper lifecycle opt-in** —
`paper_engine.run_manage_cycle(lifecycle=…)` + a `be_armed` column on `paper_positions` +
`manual_paper_lifecycle_enabled` (default OFF, the INVERSE of `captured_autoclose_enabled`) threaded
from the single `handlers.run_manage_and_refresh` chokepoint + a Settings toggle; the DRIVER's
isolated account always passes `lifecycle=False` and never reads the flag. A final code review flagged
one dormant edge — arming BEFORE `recommend()` could same-cycle break-even-stop a tiny-credit IC whose
commissions exceed the +50% threshold — so the manual-paper cycle now **arms AFTER `recommend()`**,
matching `compute.run_captured_manage_cycle` exactly (crossing cycle HOLDs via Rule 5, then Rule 3
governs). Regression-clean across all three suites (options-scanner 1407/11-baseline, options_svc
963/2-baseline, webgui 1207/0). Built + reviewed subagent-driven (per-unit TDD + a final holistic
review). Design/plan: [design](plans/2026-08-11-recommender-lifecycle-gate-and-trailing-design.md).
Commits `602528b` (fix) / `ad1ddab` (ratchet) / `321fd01` (manual-paper) / `9809e63` (arm-after).)

Prior — 2026-08-11 (**Gamma heatmap — the $NDX "comb", and Term day separators.**
The intraday heatmap rendered $NDX as a dense comb of vertical stripes instead of a smooth
blended field. It was a **rasterization** bug, not data: `interpolation:True` lays the canvas
out on ONE row height — `_strike_step`, the MEDIAN strike gap — and **$NDX is the only symbol
in the app with a mixed ladder**, quoting **5-wide near the money among 10-wide** (measured
live: 28 gaps of 5 among 56 of 10; $SPX is uniformly 5, SPY/QQQ/IWM 1, AMD 2.5). At `rowsize`
10 the 5-wide strikes collided two-into-one row and the cells between them were never written.
Two plausible suspects were ruled out with measurements first — the **candle overlay** (hid the
`columnrange`+`errorbar` groups in the DOM; stripes remained) and **missing snapshots** (the
source grid is a *perfect* rectangle, 359 columns × 48 strikes, zero nulls, and the Redis
payload matches `gex_history.db` across all 362 shared minutes). The tell that settled it:
a strike whose stored values ran perfectly smooth (`−2.56, −2.56, −2.55, −2.55…`) while the
pixels on that row went **transparent every 8th column** — smooth data under striped pixels can
only be the rasterizer. Fix = the pure **`gamma.uniform_strike_grid`**: fill the visible ladder
to its FINEST gap, linearly interpolating inserted rows between their bracketing real strikes
(inventing nothing the chart wasn't already implying — an interpolated heatmap shades between
samples regardless), real strikes untouched, a row bracketed by a missing sample left `None` so
genuine holes stay holes, an already-even ladder returned as the SAME objects (every other
symbol pays nothing), and a 240-row cap against a pathological chain. Applied to the collected
cells and the projection band. **Live-verified**: $NDX went from 40 rows on mixed 5/10 spacing
to a uniform 5.0 ladder of 65 rows and an exact rectangle (23,920 points = 368 × 65), canvas
362×42 → 370×84, periodic alpha-zero stripes gone. **The Term view was never affected** — its
axes are CATEGORICAL and points are addressed by row INDEX, so an uneven ladder cannot collide
rows there (trade-off: its y axis is ordinal, not proportional to price); a test now pins that.
Term did get **1px hairlines between expiry columns** (`expiry_separators` → xAxis plotLines at
`i+0.5`, the midpoint between category centres, `rgba(255,255,255,0.22)`, `zIndex` 5): the same
interpolation blends Term into one continuous field, but its x axis is **days**, so the blending
actively misleads — it smears one expiration's exposure into the next when nothing varies
continuously between them. Also on the page: a collapsed **"How to read the 0-DTE close
projection"** expander rendering the shared `page_help.PROJECTION_HELP_MD` (outline bars /
Proj. flip / hedge-pressure panel + the flat-spot caveat). It is mounted on the page and not
only in the nav hover guide because that tooltip is **`pointer-events:none`** and Quasar sizes
it to the space under its nav item — measured at ~466px of a ~1400px guide, so it clips to
**33%** with no way to scroll; long-form help parked there alone is unreachable, not merely
below the fold. webgui **1201** green. Commits `1098e60` + `b8672a8`.)

Prior — 2026-08-09 (**Flow Alerts screen — the alerts finally have somewhere to live**:
`options_svc` has detected options-flow alerts on every 1-min GEX tick for weeks — premium
**crossover**, contract-level **unusual activity**, dealer **gamma flip** — pushed each to the phone and
published a day-scoped list to `cache:options:flow_alerts`. The webgui read that list for exactly one
purpose: chime and toast ids it hadn't acked. **Miss the toast and the alert was gone.** The only durable
trace anywhere was the Opportunity Board's per-symbol *count*, which tells you a symbol fired without
telling you what or when. New **`/options/flow`** is a pure Tier-1 reader of that same key — no new
service, command, or cache key — mounted as a standalone **left-rail item under Options** beside
Opportunity Board (a market-wide read, deliberately not a step in the Options strip's per-signal
find→analyze→track→repair workflow). A chronological table, **newest first**: Time (CT) · **Age** ·
Symbol · Type · Side · Detail · Alert, with per-type detail cells (`$1.20M calls vs $400k puts` /
`0DTE 737C · 12,400 vol / 1,100 OI (11.3×) · $2.13M` / `spot 6412 vs flip 6400`) tinted green/red from a
finite `(type, side)` → Tailwind class map. Kind + symbol filters run **client-side** over already-read
rows so toggling is instant, and **one 2 s timer serves two cadences** — the payload is re-read only when
the cache version moves, while the Age column recomputes against the rows already on screen, so age stays
live without churning the table. **Click any row → Dealer Positioning on that symbol** (`handoff`'s new
one-shot `gamma` stash, consumed at `gamma.render()`'s existing build-time symbol sync, where the dropdown
is already set *before* `on_value_change` is wired — so the handed symbol beats the cached one without a
spurious refresh, then one explicit refresh moves the snapshot to it). **Two Tier-2 defects surfaced while
building it, both confirmed against the live key**: the published list was capped at **50** and the live
payload held **exactly 50** — it had been silently dropping the morning's alerts (now **300**); and
`flow_alerts.detect_uoa` **never emitted a `ts`** at all, so **only 18 of those 50 alerts carried a
timestamp** (the 15 crossovers + 3 gamma flips) while all 32 unusual-activity alerts had none — nothing to
place a third of the tape on a timeline with. The drain loop now stamps the detecting tick. **Restart
`options_svc` + the webgui**; UOA timestamps appear only on alerts published *after* that restart, so
pre-existing rows legitimately render a blank Time. Scope is deliberately **today only** — no history, no
date picker, no nav badge, and the existing toast/chime/phone-push/Settings toggle are untouched. Also
corrected: the drawer-icon test's docstring miscounted its own scope (claimed 3 groups + 3 rail pages when
there were 4 and 2). webgui **1102** green; options_svc **932** green (its 2 date-relative
`test_expected_move` failures are the documented baseline). Design/plan:
[design](plans/2026-08-09-flow-alerts-screen-design.md) / [plan](plans/2026-08-09-flow-alerts-screen.md).)

Prior — 2026-08-09 (**Dev / prod cutover PERFORMED, plus the launcher guards it exposed**:
both environments now run simultaneously and were verified live — prod on 8100/8210-8215/8500 from
`D:\WebGUI Trading Prod`, dev on 9210-9215/`:9500`/Redis db 1 with all four suppressions active, one
shared Memurai, dev holding **no proxy of its own**. `IS_DEV=True` was confirmed for real outside pytest
(the `DEV` chip renders, the Status page withholds the proxy and Memurai restarts, everything dev owns
stays restartable) — the check the suite structurally cannot do, since pytest pins identity to prod.
**Four defects surfaced in the first hour of real use, all in launchers, all invisible as failures.**
(1) A **PROD launcher run from the DEV checkout** starts a *proxy*, and dev's `PROXY_PORT` IS prod's
`:8100` — so it bound prod's port while prod never started, and everything looked healthy while a
dev-checkout process served prod's market data. `start_all{,_wt,_hidden}.bat` now probe
`repo_paths.IS_DEV` and refuse; in `start_all_hidden.bat` the guard sits ahead of the `__hidden`
dispatch so it fires on the VISIBLE pass, before the self-relaunch and before the HUD. (2) **Starting a
stack twice** spawns duplicates that each complete a full startup — real Schwab calls, a sentiment
backfill — before failing to bind and exiting; all four launchers now call new
**`tools/check_stack_down.py`**, which imports **`stop_all._targets()`** so the starter and the stopper
cannot disagree about what this environment owns, with `--only LABEL` for single-process launchers and a
degrade-to-allow on a probe it cannot run. (3) **`start_dev.bat` hung** when dev was started before prod:
every tab blocks on `wait_and_run 8100`, nothing binds `:9500`, and `:wait_web` looped forever on a
message naming the web GUI rather than the proxy underneath it — the WT branch now calls the bounded
`:wait_prod_proxy` first and `:wait_web` returns a failure with the real diagnosis. (4) **`start_webgui.bat`
hardcoded `:8500`** in its title, banner, proxy hint and browser helper; from dev it started on `:9500`
while announcing prod's port and opening a browser there. It now derives everything from `repo_paths`
and `_open_webgui.bat` takes the port as an argument. ⚠ two batch metacharacter traps cost three rounds there — `for /f "usebackq"` quote-stripping and
`%` being eaten inside a `-c` argument — both written up with the working shape in the runbook's
**Gotchas**, since that is where someone editing a launcher will look. Also fixed: the **`DEV ·` tab-title prefix never applied** — `_layout` calls
`ui.page_title()` on every page, overriding `ui.run(title=…)`, so `document.title` read "Market Scanner"
in BOTH environments; `window_title()` now takes the page label. And **`snapshot_from_prod.py` imported
`redis` AFTER the SQLite copy loop**, so a wrong interpreter would have written ~1.4 GB and then failed,
leaving dev with fresh stores and stale Redis. Both caught by USING the thing, not by testing it.
Operator runbook: [`docs/dev-prod-environments.md`](dev-prod-environments.md).)

Prior — 2026-08-08 (**Dev / prod environments — two checkouts, one machine, running at once**:
the always-on stack moves to a new **prod** clone at `D:\WebGUI Trading Prod` pinned to `main`, and
today's folder becomes **dev**. Prod keeps every current port (proxy `:8100`, services 8210-8215, webgui
`:8500`, Redis db 0) so standing it up is a **relocation, not a reconfiguration**; dev shifts to
9210-9215 / `:9500` / Redis **db 1** and **starts no proxy** — it BORROWS prod's, because the Schwab OAuth
**refresh token is a single rotating credential** and two proxies holding it can invalidate each other's
session. **Accepted consequence, stated up front: dev's on-demand fetches need prod's proxy up.**
**Identity comes from a GITIGNORED `config/env.local.toml`** (`name`, optional `peer_root`) against the
**tracked** `config/environments.toml` profiles — **absent ⇒ prod**, so a checkout without a marker
behaves exactly as this repo did before, and because the marker is gitignored **`git pull` can never
carry an identity between checkouts** (the deciding property over an env var, which anything launched
another way would lack, and over folder-name detection, which would make renaming a folder change a live
stack's behaviour). Resolution lives in **`repo_paths.py`**, not a new module — it already parses
`ports.toml` and is imported by ~40 files — so every consumer (services, launchers, `tools/stop_all.py`,
`tools/restart_one.bat`, the Status page) follows the environment with **no edit of its own**.
**Four suppressions, ~six one-line guards, each reusing a degrade path the code ALREADY has** so a
suppressed dev cannot take a code path prod never takes: `allow_notifications` →
`shared/notify/channels.load_config` recursively zeroes **every** `enabled` key **LAST**, after the
`NOTIFY_ENABLED`/`TWITTER_ENABLED` env escapes, because the **X/Twitter poster has its own gate that
never consults the master switch and is the one channel that PUBLISHES**; `allow_claude` → the three
client factories return `None` and fall into the existing no-API-key path; `schedulers` →
`services/_scaffold.make_app` skips the wiring, **command handlers still run** so the UI is fully usable
off a snapshot; `autonomous_trading` → `driver_svc.handlers.run_autonomous_cycle` early-returns —
deliberately redundant with the scheduler skip, because `cycle` is **also a command** and the arm state
lives in Redis, so a snapshot carrying an enabled `cache:driver:control` would otherwise have dev
paper-trading. `TRADING_ENABLE_SCHEDULERS=1` is the one escape hatch (real Schwab calls; use it only when
the collectors themselves are what you're testing). **Under pytest the process PRESENTS AS PROD** — ports,
Redis DB, `owns_proxy` **and `ENV_NAME`** — with all four suppressions forced ON, which is what lets the
existing suites pass unchanged inside a dev checkout; the cost is that **dev's own `IS_DEV` branches are
only ever exercised by monkeypatch**, so confirming dev really withholds the restart buttons is a MANUAL
check. **Cross-environment rails** close three hazards that were already latent: `stop_all` would have
killed **prod's proxy** from dev (`PROXY_PORT` *is* 8100 there) and **prod's HUD** (it binds no port, so
it is matched by command line — now root-scoped); and the Status page would have offered a restart that
bounces prod's proxy or the shared Memurai service (proxy card now read-only "shared — owned by prod",
Memurai restart hidden in dev). Dev's webgui carries a **`DEV` chip** + tab-title prefix — two
identical-looking NeuralStrike tabs writing to different books is a mistake waiting to happen.
**`tools/snapshot_from_prod.py`** (run FROM dev) copies prod's SQLite via the **online-backup API so prod
keeps running and writing** through a ~1.4 GB `gex_history.db`, then `DUMP`s db 0 → db 1; it hard-refuses
unless `ENV_NAME == "dev"`, refuses when both Redis DBs resolve equal (the copy FLUSHDBs first — equal
indices would wipe prod's cache), refuses while dev is up, **excludes `cmd:*`** (a stream is a queue dev
would drain and EXECUTE — a stranded `driver_paper_create` would double-open) and **rewrites
`cache:driver:control` disabled**. `start_dev.bat` (7 processes, no proxy, refuses outside a dev-marked
checkout) and `tools\promote.bat` (prod-only, **dirty-tree guard BEFORE it stops anything**, `git pull
--ff-only`, reinstall only if `requirements.lock` moved, restart) are the two new launchers.
**The cutover itself is a human checklist and has NOT been run** — no prod clone exists yet, and the
first real snapshot is untested against a live prod. **Operator runbook:
[`docs/dev-prod-environments.md`](docs/dev-prod-environments.md)**, which also carries the seven
**known limits** — chiefly that dev is *quiet at rest, not incapable* (command handlers are ungated, so
clicking Run scan in dev still reaches Schwab), that the legacy `notifier.py` modules sit outside the
notification gate (dead from every service path, runnable by hand), and that restarting Memurai takes
both environments down. Design/plan:
[design](docs/plans/2026-08-08-dev-prod-environments-design.md) /
[plan](docs/plans/2026-08-08-dev-prod-environments-plan.md).
**Two stale test facts corrected on the way through, both re-measured:** the webgui suite is **1053**
(it was **1040** at the pre-feature commit `7667920` — the previously recorded **1049** was simply
wrong), and **`services/sentiment_svc` carries a PRE-EXISTING failing test**,
`test_compute_regime.py::test_daily_history_wins_over_session_latch` (**250 passed / 1 failed**),
reproduced at `7667920` so it predates this branch — documented under "Tests" alongside the two
`test_expected_move` baseline fails so nobody mistakes it for a regression.)

Prior — 2026-08-08 (**GEX grid storage → columnar float32 + mmap (SQLite stays; Postgres measured
SLOWER)**: a storage-efficiency change to `gex_history_db.py` driven by a measured profile of the live
1.54 GB `gex_history.db`. **The finding that drove everything: SQLite is NOT the bottleneck** — decomposing
the hot paths showed the engine is **4% of read time and 7% of write time**; `json.loads` alone was **68% of
the read path** (7.0 ms SQLite fetch vs 114 ms JSON parse on a 437-row session). A **PostgreSQL migration was
evaluated and rejected on measurements**: a local client/server round-trip costs ~0.2–0.3 ms (anchored on a
measured 0.160 ms Memurai loopback PING) against SQLite's in-process microseconds, making the per-symbol reads
that run 93×/min **4–5× SLOWER**, the big grid read ~10% slower, and writes roughly par only if batched — while
leaving the 93–96% serialization cost untouched. Postgres' genuine wins here are operational (autovacuum), not
speed. So: **keep SQLite, fix the serialization.**
**The change.** The grid is a regular numeric table (`{strike: {call, put, net}}`, ~250–360 strikes) that was
stored as JSON text inside zlib — the worst format for it. It is now a **columnar float32 blob**:
`b"G1"` + zlib(`<I` count + n float32 strikes + n×3 float32 call/put/net), strikes **sorted** (which also
compresses better). Measured on the heaviest real case (`$SPX`/gex, 437 rows × 362 strikes): **decode
152.9 → 61.6 ms (2.5×)**, **encode 352.7 → 120.2 ms (2.9×)**, **blobs 726 → 527 KiB (1.38×)** — the live DB's
**898 MiB of grid payload projects to ~652 MiB** as rows roll through the 5-session retention. `connect()` also
sets **`PRAGMA mmap_size=1 GiB`** (`_MMAP_BYTES`, both read-only and read-write): one (symbol, view, session)
read touches ~437 **scattered** pages — the collector writes every symbol each minute, so consecutive rows of a
key land **360 rowids apart** and essentially every row sits on its own page — and mmap drops a syscall + buffer
copy per page (4.5 → 3.0 ms median warm).
**The columnar path is SHAPE-GATED, and that is load-bearing.** Grids are documented to carry
ints/strings/None/nested dicts (pinned by `test_gex_history_efficiency.test_encode_grid_handles_non_float_and_nested_values`),
which this layout cannot express — so `_pack_columnar` returns `None` for anything but three plain numbers per
cell and `_encode_grid` **falls back to the JSON path**. `bool` is rejected explicitly (it is an `int` subclass
and would silently flatten to 1.0/0.0). Measured across 1,500 random live snapshots, **100% of real cells are
exactly `{call, net, put}` floats**, so the fast path covers all production data at zero cost to the flexible
contract. **Forward-only** (like the 2026-07-25 zlib change): nothing is backfilled and `_decode_grid` now reads
**three** formats — `b"G1"` columnar, other `bytes` = zlib JSON, `str` = plain JSON. `orjson` (~2× on that legacy
JSON path) is used **with a stdlib fallback and is NOT a declared dependency** — it only rides in transitively via
nicegui, which `options_svc` does not depend on.
**float32 is safe, with ONE documented caveat you should know.** Values are ALREADY rounded to
`_GRID_SIG_FIGS` = 6 significant figures and float32 carries ~7.2 — measured max relative error **5.96e-08**
across 471,657 real cells, and strikes round-trip exactly (an off-by-epsilon strike would split a grid key and
break the ±N crop). **BUT float32's smallest normal is 1.18e-38, and deep-OTM strikes carry BS gamma/vanna
underflow like `3.77e-163` — 88.6% of cells in a real `$SPX`/gex snapshot (25.9% across a random sample) flush
to 0.0.** This was **verified display-neutral, not assumed**: across 600 real snapshots there were **0 call/put
wall disagreements**, **0 display-significant cells lost** (nothing above 1e-9 of the snapshot max), and a max
8.85e-07 error on summed net_total — and `flip`/walls/`net_total` live in their OWN columns computed from the
FULL chain, so grid encoding cannot touch them. If bit-exactness is ever required, float64 values + float32
strikes still give ~9× decode at today's file size.
**Decoded grids return plain Python floats (via `.tolist()`), NOT numpy scalars** — the grid is JSON-serialized
into `cache:options:gamma`, and a numpy scalar would blow up `json.dumps`; pinned by a test.
**Measured and REJECTED — don't retry these:** **`WITHOUT ROWID`** (the obvious fix for the page scatter) made a
62,560-row DB **59% larger (114 → 181 MB) and 4× slower to write (1.7 → 7.3 s)** — SQLite's guidance is that it
suits *small* rows, and 1.3 KB blobs are the documented anti-pattern; **window-sliced decode** (decoding only the
±20 strikes the crop keeps) was **no faster** than a plain columnar decode because zlib dominates; **`cache_size=256MB`**
measured as noise. **Still open (not done here):** the **cold-read** problem — first-touch reads of a session ran
**1,755–2,746 ms vs 42–128 ms warm (14–42×)** because 564 KiB of data is spread across 22% of the file; the right
fix is a **nightly clustered rebuild** (`INSERT … ORDER BY symbol, view, ts`, hooked onto the existing purge), NOT
`WITHOUT ROWID`. Also open: preserving the dict contract costs most of the theoretical win — returning arrays
instead of `{strike: {call, put, net}}` measured **5.7×** end-to-end vs the **2.5×** shipped, but that needs a
consumer-contract change across `gamma_tool.get_directional_walls` / `_crop_gamma_views` / `_level_track`.
**Restart `options_svc`** (new rows write the new format immediately; old rows keep decoding).
options_svc **916** green (+ the 2 documented `test_expected_move` date-relative baseline fails); new
`options-scanner/tests/test_gex_grid_columnar.py` (14 tests) + an mmap test; TDD throughout. **Pre-existing, NOT
caused by this change:** `options-scanner/scripts/fix_gex_history_scale.py::_rescale_grid` does a bare
`json.loads` on `gex_json`, so it has been unable to read grids since compression shipped 2026-07-25 (its tests
pass only because they use uncompressed fixtures) — it is a one-time migration that already ran.
Prior — 2026-08-07 (**part C — `em` de-duplicated against `pop` in the scoring weights
(12 → 6)**: `em` and `pop` are both monotone in how far OTM the short strike sits — `pop` via market
delta (`pop_pct = (1 − |delta|)·100`), `em` via distance ÷ expected move — so at 12 + 10 they spent
**22 of 100 on ONE axis, counted twice**. Measured on **2,190 real strike observations** (8 symbols ×
11 DTEs, live chains): Spearman **ρ = +0.928 pooled**, **+0.892 within the traded |delta| ≤ 0.27 band**,
+0.93 to +0.99 per symbol. **DOWNWEIGHTED, NOT DROPPED** — `pop` carries skew (market delta) while `em`
is skew-free (ATM IV), so ~11% of the rank variance is genuinely independent and dropping `em` would
discard the geometric read. The distance axis goes **22 → 16**. **The redundancy is MEASURED; the
redistribution target is NOT** — `signal_outcomes` is 95% `MANUAL_CLOSE`, so realized P&L reflects
closing behaviour as much as entry quality and there is no basis for saying which factor earns the freed
weight. The 6 points are therefore spread roughly proportionally across every factor EXCEPT `pop`
(adding them there would leave the axis exactly where it started): rr 15→16, theta 10→11, iv 12→13,
iv_hv 10→11, vega 8→9, trend 10→11. **⚠ This is the SECOND loosening change of the day** — `em` scores
systematically LOW (29–44 on the live set vs 50–86 for the other factors), so cutting its weight lifts
composites: measured **+0.3 to +2.6** on live signals, with one (SMCI) crossing INTO
`signal_recorder.MIN_SCORE = 58`. Stacked on part B's +1.1 to +2.0, **the effective strictness of the 58
capture floor has drifted materially today and MIN_SCORE was deliberately NOT retuned to compensate** —
that needs its own decision once a day of post-change capture volume is observable. options-scanner
**1328 passed / 16 failed** (documented baseline groups only, verified by comparing the failing SET);
ruff clean. TDD.
Prior — 2026-08-07 (**part B — the EM-BUFFER SCORING FACTOR now sizes to the trade's own
horizon and the expiration's own IV**: `scoring.calc_composite_score` read
`expected_moves["monthly"]` — a **30-day** EM — for **every** trade regardless of DTE. Two defects
followed. **(1) The factor measured DTE, not strike placement:** a short sitting at exactly 1× its own
expiration's EM (the factor's stated 0-point) scored **9.1 at 1 DTE vs 50.0 at 30 DTE** — IV cancels out
of that ratio, so it held at any vol. **(2) It went nearly inert at short DTE:** across a realistic
0.5–2.5× strike sweep its usable spread collapsed from **75 points at 30 DTE to 18.3 at 1 DTE**, i.e. at
weight 12 it could only move the composite ~2.2 points. **Within one DTE bucket the bias is monotone, so
ranking there was already preserved** — the reason this was never visible on the Scanner's tabs. **The
real victim is the DRIVER:** `driver_svc.compute.build_packet` merges `signals_0dte + signals_swing`
into ONE composite-ranked menu, so the live autonomous decider was comparing 0-DTE against swing on a
score that systematically penalised the short-dated side by up to ~5 points. `em_1sd` now comes from
`calc_expected_move(underlying, iv, signal["dte"])`, preferring the expiration's own ATM IV — stamped as
**`expiry_iv`** by `screen_spreads` at all THREE signal-construction sites (both verticals + the iron
condor, which inherits its put side's stamp; missing it there would have left ICs scoring off the
symbol IV while their component verticals used the expiry's) — then the symbol-level `current_iv`, then
the legacy monthly EM so a stale or hand-built signal dict still scores rather than crashing or zeroing.
**`USE_EXPIRY_EM = False` reverts BOTH halves**: it suppresses the stamp too, so scoring falls back to
the symbol IV — the kill switch is a full revert, pinned by a test. **This moves scores UP at short DTE,
the OPPOSITE direction from the 2026-08-06 quality cut**, so it loosens rather than tightens: more
signals clear `signal_recorder.MIN_SCORE = 58` and `NEG_GEX_MIN_SCORE = 62`, and stored `entry_score`
values in `signals.db` stop being comparable across the change. **The 50-point directional/Finder cut is
NOT affected** — that runs on `strategy_scoring`, a separate model. **Measured on the live scan:** em
factor **+9.1 to +16.4**, composite **+1.1 to +2.0**, and **0** signals newly crossing the 58 floor —
but every live signal that moment was 14 DTE, so this **understates** the effect; the +4 to +5 shifts
live at 0–3 DTE, where no signals existed to measure. options-scanner **1327 passed / 17 failed** (the
documented baseline groups only — verified by comparing the failing SET, not the count); ruff clean.
TDD (red → green).
Prior — 2026-08-07 (**part A — EM strike window sized off the EXPIRATION's own IV, not a 30-day IV**:
`screen_spreads` now sizes each expiration's expected-move strike window from **that expiration's ATM
IV** instead of the symbol-level ~30-DTE IV `iv_analysis.extract_atm_iv` returns. New pure
`iv_analysis.expiry_atm_iv(chain, exp_str, underlying)` (averages the ATM call+put IV for ONE
expiration; None when absent) + `expiry_daily_em(price, iv_pct)`, consumed by
`scanner_engine.effective_daily_em(chain, exp_str, underlying, fallback)`, which is called once per
expiration inside `screen_spreads` and passed to `is_strike_in_expected_move_window` **in place of the
caller's daily EM**. **The KEY design decision: only the VOL INPUT changes.** `effective_daily_em`
returns a DAILY-equivalent EM, so the `√dte` period scaling, the 0-DTE hours-to-close decay, and every
`ZERO_DTE_BUCKET_EM_CURVE`/`SWING_EM_CURVE` multiplier are byte-for-byte untouched — the blast radius is
one number. Degrades to the caller's IV30-derived EM whenever the per-expiry IV is missing (**never to
0**, which would disable the filter and silently accept every strike). Kill switch
**`USE_EXPIRY_EM = True`** restores the old behavior exactly. **`daily_expected_move` is unchanged
everywhere else** — `MOMENTUM_VETO`/`intraday_move_ratio`, the Expected Move page and the gamma
briefings still use the symbol-level value by design. **Measured live on 4 symbols (2026-08-07):** the
old path **UNDERSTATED 0-DTE EM by 33-56%** (SPY front IV 16.0 vs IV30 10.3) → 0-DTE shorts were sized
too CLOSE and now move further OTM; it **OVERSTATED 3-DTE by 14-24%** → those shorts move closer and
collect more credit; 4-7 DTE moves ≤5%. The 3-DTE case is largely the **weekend effect** — Fri→Mon is 3
CALENDAR days but 1 trading day, and `√(calendar dte)` over-counts it, while the front expiry's own IV
prices it correctly. This is a term-structure correction, **NOT a widening**: the direction flips with
the curve. **Restart `options_svc`.** **Deliberately NOT changed: `scoring.calc_composite_score` still
takes its EM-buffer factor from the MONTHLY (30-day) EM regardless of the trade's DTE** — a related
defect, but moving it would shift every composite score and collide with the 58 capture floor and the
50-point directional cut, so it needs its own decision. **Two existing `TestScreenSpreadsStrikeValidity`
tests broke and were repaired at the FIXTURE, not the assertion:** their mock chain carried 25.0 vol
while the tests passed `daily_expected_move=8.0`, and the short strike sat EXACTLY on the 2.50× boundary
(distance 20 = 2.50 × 8.0), so the chain-derived 6.94 pushed it out. `_mock_chain` now defaults to
**`_EM8_VOL = 28.84`**, which reproduces a daily EM of exactly 8.00 at underlying 530 — every window
boundary those tests were written against is preserved. **Caught only because the failing SET was
compared, not the count: the suite went 17 → 17 while two order-varying `test_dashboard_*` cases
happened to move to skipped, masking two genuine regressions.** options-scanner **1319 passed / 15
failed / 2 skipped** (the documented baseline groups only); ruff clean. TDD (red → green).
**An audit of the `signals.db` credit units ran first and found NO bug** — `width = |K1−K2|`, `credit`
per-share, and `max_loss = width − credit` hold exactly on every row. The apparent discrepancy that
prompted the audit was an error in MY earlier analysis: converting delta→σ with a skewless lognormal,
which is badly wrong for high-IV names where `σ√T` is large. Re-measured directly in ×EM terms across a
17× IV range (SPY 8.7 / MU 71 / NBIS 153), the credit-gate boundary sits at **~1.0× EM at every IV
level**, and 1.5–2× EM fails universally — NBIS at 2× EM prices *negative* credit. Recorded because the
1.5–2× EM proposal was evaluated against it and **not** adopted.)
Prior — 2026-08-06 (**Quality cut on BOTH finder pages — only non-Weak candidates scoring
≥ 50 are emitted.** Two independent cuts, one per page, sharing a value but not a constant.
**(A) Strategy Finder (`/options/swing`)** — `compute.swing_scan` drops any scored candidate below
**`SWING_MIN_SCORE = 50.0`** or carrying **`SWING_EXCLUDED_GRADES = ("Weak",)`**, applied to **EVERY
family** (directional, debit + adapted credit verticals, iron condors) because they land in ONE
jointly-ranked table — cutting only one family would leave Weak iron condors ranked above directional rows
that were removed. The cut runs **before `assign_ids`**, so every emitted row is addressable and the
detail-panel lookup can't miss one. **This reverses the "keep Weak rows here" call recorded in the
2026-06-30 quality-gated-grading design** (that the Finder is a research tool where seeing *why* a
structure fails is the point) — the user chose a shortlist on both pages; `grade_reason` still renders,
it just only ever reads Good/Marginal/Strong now. **An empty table needed to stop lying:** a working scan
whose every candidate fails the bar rendered a bare "0 swing signals.", indistinguishable from a failed or
off-hours scan, so `swing_scan` returns an additive **`filtered_out`** count (0 on both degraded early
returns too — the shape stays uniform so the page never special-cases a missing key), the handler carries
it onto `cache:options:swing`, and the page's new PURE `swing.status_text` appends "N below the quality
bar." — omitted when nothing was dropped. **Restart `options_svc`** (Tier-1 needs no restart; the page
reads whatever the payload carries). Measured on the test fixture, the cut is genuinely cross-family:
BULL_CALL 74.3 / BEAR_PUT 66.9 survive while all four directionals (39.0) **and the adapted PCS (34.6)**
and IC (39.0) are dropped. Four existing `swing_scan` tests take an `unfiltered_swing` fixture zeroing
both constants (their subjects — family coverage, the PCS state tilt — are Weak under the real scorer, so
the assertions would have gone vacuous); 5 new compute tests + 2 handler tests + 4 page tests cover the cut.
**Unrelated fix found on the way:** `test_run_flow_alerts_emits_uoa_from_stash` /
`test_run_flow_alerts_uoa_excludes_vix` never stubbed `_load_flow_series_for`, so they read the **live
`gex_history.db`** — today's real SPY premium crossover started injecting a second alert and they began
failing on a clean tree. Stubbed to `[]` like their siblings (same class as the documented
pytest-must-isolate-on-disk-stores trap). options_svc **923 passed / 2** (the documented date-relative
`test_expected_move` fails); webgui **1044** green; ruff clean.
**(B) Market Scanner — Directional tab** (the same session, shipped first): `scanner_engine.run_full_scan`
now DROPS a single-leg directional candidate that scores
below **`SINGLE_LEG_MIN_SCORE = 50.0`** on `strategy_scoring`'s Fit+Quality composite, or that carries an
excluded grade (**`SINGLE_LEG_EXCLUDED_GRADES = ("Weak",)`**), instead of publishing it to
`signals_directional`. The tab is a shortlist of tradeable ideas, not a dump of everything
`build_directional` can construct. **Engine-side only** — `options_svc` still passes the list through
unchanged and the webgui still renders whatever arrives, so there is no contract, cache-key or page change;
**restart `options_svc`** (its next scan republishes) and note that directional signals already frozen into
**today's** `cache:options:scan_day` union stay until the date rolls (the day key carries forward what was
published, by design). **The two cuts are REDUNDANT today and that is deliberate**: a hard-gate failure pins
the composite at `strategy_scoring.GATE_FAIL_CAP` (39) and the post-grade `state_family_tilt` adds at most
+6, so a Weak candidate tops out at **45** and can never clear 50 — but they express different intents ("no
low-scoring trades" vs "no gate-failing trades") and either constant can move alone, so a **synthetic probe
test** (grade Weak at score 90) pins the grade half, matching the existing redundant-defense convention.
**The cut runs BEFORE the per-symbol `SINGLE_LEG_MAX_PER_SYMBOL` slice** — filtering after it would let a
symbol whose top-scoring rows are Weak spend its cap slots on rows that are then dropped; score alone can't
show this (the list is sorted desc), so the ordering test binds through the GRADE half. **Expect materially
fewer directional rows on BOTH pages, unevenly by side:** live data measured **LONG_PUT avg 59.2 / LONG_CALL avg 45.2**,
so the cut amplifies the **documented pre-existing scoring artifact** — a long put's max profit is bounded at
S=0 so it gets a finite R:R, while a long call's is honestly unbounded → `rr=None` → a PoP proxy ≈14 points
lower. Long calls will now largely vanish from the tab. That artifact is a scoring-model decision, NOT fixed
here. Four
existing `TestDirectionalSignals` tests (shape / window coverage / ordering / per-window EM) now take an
`unfiltered_directional` fixture that zeroes both constants: **every** candidate the fake chain builds grades
Weak (23–39), so under the production cut those assertions would go vacuous against an empty list — the
fixture preserves their original intent, and the cut has its own four tests. TDD (red → green).
options-scanner **1309 passed / 17 failed** (the documented pre-existing baseline set: `test_dashboard_*` ·
`test_gex_collector*` · `test_key_levels_doc` · `TestEarningsAvoidance` — the passing count was measured on
this tree and supersedes the stale 1286); options_svc **916 passed / 2** (the documented date-relative
`test_expected_move` fails); webgui `test_options_scanner` **77** green; ruff clean.)
Prior — 2026-08-05 (**Explain now carries the 0-DTE drift too — and a wrong-baseline bug
caught in the process**: an audit of the three buttons found **Analyze** and the **scheduled briefings**
already correct (`calc_all_from_chain` → `build_analysis_dict` → `format_pressure_panel` picks up
`hedge_drift_by_strike`, so the CORRECTED projected flip flows through — live-checked at
`projected_flip 7721.68` against the chart's `7721.66`), but **Explain** was not: its `GammaRead` had no
hedge fields at all, so the infographic never mentioned the 0-DTE drift even though
`snapshot_summary(dex)` already carried it. `GammaRead` gains optional `hedge_pressure` /
`hedge_direction` / `projected_flip` / `delta_flip`, `build_gamma_read` populates them (direction via a
new `_hedge_direction` mirroring the engine's sign rule), and `_derive` folds one sentence into
**`dex_read`** so BOTH renderers pick it up with no template change. All four fields are None off a 0-DTE
book and the sentence then vanishes entirely.
**The bug worth remembering:** the first version read *"taking the flip to 7,722 (now 7,733)"* — pairing
the projected DELTA flip with the **GAMMA** flip. Those are two different curves: measured live, gamma
flip **7733.49**, delta flip **7720.24**, projected **7721.68**. So it implied an 11-point DOWNWARD move
when the real drift is **+1.4 points UP**. Fixed by carrying `delta_flip` as the baseline and labeling it
*"delta flip"* explicitly; a test now pins that the baseline is the DEX flip, because the two numbers look
equally plausible side by side and only the label distinguishes them. **Restart `options_svc`.**
Live-verified: *"0-DTE charm alone moves +$2.79B of delta — dealers must buy if spot holds, taking the
delta flip to 7,722 (now 7,720)."* options-scanner **1309 passed / 11 documented-baseline fails**
(Tk-dashboard files excluded — the documented intermittent access-violation crash); options_svc **918**
(+2 documented `test_expected_move`); webgui **1055** green.
Prior — 2026-08-05 (**Projected DEX bars — each strike's own 0-DTE charm drift**: the
by-strike bar chart now overlays a **"Projected close"** outline showing where each strike's net delta
lands once its OWN 0-DTE drift is applied (`net + hedge_drift_by_strike[strike]`). This is the WHERE to
go with the projected-flip line's WHAT: the flip says the crossing moves, these bars say which strikes
move it. Reuses the per-strike map built for the flip fix — **no new engine math, no new collection**.
**Drawn as an OUTLINE ON TOP of the solid bar** (transparent fill + amber `PROJ_FLIP_COLOR` border,
matching the projected-flip line) rather than a filled bar behind it: a bar behind is invisible whenever
the projection pulls BACK inside the current bar, so only an outline reads in both directions —
extension and contraction. `plotOptions.bar.grouping` is **False** so the outline OVERLAYS its bar
instead of being drawn beside it, which would halve the bar width and break the pixel-alignment with the
heatmap. **Only strikes that actually carry 0-DTE interest get an outline** (`bars_from_gex` returns
`projected=None` elsewhere) — otherwise most outlines would sit exactly on their solid bar as noise —
and the whole series is **omitted** when the symbol has no 0-DTE book, i.e. most symbols most of the
time. The page re-floats the drift map's keys via `_refloat_keys` exactly as it does the grid (Redis
JSON stringifies float keys). **Restart `options_svc` + the webgui.** Live-verified by injecting a
realistic near-money drift map: 6 outlines drawn on the DELTA view, amber stroke over transparent fill,
and for a strike whose current net is 0 the solid bar measured **0px** against a **297px** projected
outline — the extension is unmistakable. A Tier-1 guard (`test_page_imports_no_engine_or_proxy`) caught
a comment naming `gamma_tool` in webgui source and was left strict; the comment was reworded instead.
webgui **1053** green.
Prior — 2026-08-05 (**0-DTE hedge-pressure history panel**: a compact signed-column track of
`hedge_pressure` across the session, mounted **directly under the heatmap** and sharing its time
categories. It is its **OWN chart element, not a heatmap overlay** — pressure is in DOLLARS while the
heatmap's y-axis is STRIKE *and* is pixel-aligned to the bar chart, so it cannot share that axis.
Values are plotted in **$B** (raw dollars run to 1e9+ and are unreadable) and colored **per point by
sign** — green = dealers must BUY into the close, red = SELL — so the moment pressure flips side is
visible at a glance; one series carries both. A one-line reader (`hedge_summary_text`) states the CURRENT
value and direction. The panel + label are **hidden unless the symbol has a 0-DTE book**, and they hide
wherever the heatmap does (all six `set_visibility(False)` sites), so they can never outlive it.
**No new collection was needed** — `hedge_pressure` had been stored since 2026-07-30, so the track works
**RETROACTIVELY** on ~391 rows/session for the 0-DTE names (`$SPX`/`$NDX`/`SPY`/`QQQ`/`IWM`/`AMD`). New
`gex_history_db.load_hedge_series(conn, symbol, d=None)` reads the `dex` rows chronologically
(`(ts, hedge_pressure, net_delta_0dte, projected_flip)`, sargable ts range) and **SKIPS NULL-pressure
rows rather than returning them as zero** — the column is only populated while a symbol's nearest expiry
is today, and a zero would read as "no drift" instead of "no 0-DTE book". `compute.gamma_snapshot`
attaches it as **`hedge_history`** on the SAME read-only connection and through the SAME `_rth_only`
window as the heatmap rows, so the panel's x-axis lines up with the heatmap above it. The
`projected_flip` element of each row is **forward-only** (that column shipped 2026-07-28), so historical
rows carry None there while still reporting pressure — a projected-flip track becomes possible as data
accrues. **Restart `options_svc` + the webgui.** Live-verified on the real DB: 391 points,
**349 green / 42 red**, peak **+3.37B**, trough **−0.21B**, rendering as 391 ~1px paths (the correct
density for 1-min samples). webgui **1049** green; options-scanner **1306 passed / 16 documented-baseline
fails**; options_svc **916** (+2 documented `test_expected_move`).
Prior — 2026-08-05 (**Projected EOD delta-flip line + a CORRECTED projection**: the Gamma
heatmap gains a dashed amber **"Proj. flip"** level on **all four views** — where the DEX curve crosses
zero once the 0-DTE book's deltas are advanced to the 15:00 CT close by **charm** at flat spot. It is a
0-DTE DELTA concept, not each view's own metric, so it is computed ONCE (`compute.gamma_snapshot` →
`projected_flip`) and drawn everywhere as a shared reference; the gap between it and the actual flip IS
the hedging drift, expressed in price. **`None` whenever the nearest expiry isn't today** — i.e. most
symbols, most of the time — in which case no line is drawn.
**The engine calculation had to be fixed first, and that is the part worth remembering.**
`gamma_tool.compute_projected_flip` spread the TOTAL `hedge_pressure` evenly across every strike
(`hedge / n`). Charm drift is NOT uniform — it concentrates in near-the-money 0-DTE strikes — so
averaging it over the whole chain (including deep wings holding no 0-DTE interest) lifted the entire
curve: measured on live `$SPX`, it erased **56 of 57 negative strikes** and moved the crossing to
**~9,600 with spot at 7,791**. New `gamma_tool.project_0dte_drift_by_strike(contracts, spot,
hours_to_close)` attributes each contract's drift to ITS OWN strike (`OI × (delta_proj − delta) × 100 ×
spot`, the same clamped charm projection `project_0dte_pressure` uses, so the TOTAL is unchanged — a
REDISTRIBUTION, not a new number); `calc_dex_from_chain` + `calc_all_from_chain` expose it as
**`hedge_drift_by_strike`** and `compute_projected_flip` now consumes it. It deliberately **does NOT fall
back** to the flat average — without a per-strike map there is no honest projection, so it returns None.
**This also fixes the Gamma briefings**, which were being fed the old numbers via `build_analysis_dict`.
Two tests that encoded the flat-average contract were updated (intent preserved).
**Collection for a future history track:** `hedge_pressure` / `net_delta_0dte` /
`projected_net_delta_close` were **already being stored** (52,808 rows since 2026-07-30, `dex` rows only —
only 0-DTE names such as `$SPX`/`$NDX`/`SPY`/`QQQ`/`IWM`/`AMD` ever populate them), so a hedge-pressure
time series is available **RETROACTIVELY**. The per-strike drift map is grid-sized and deliberately NOT
stored; instead the resulting level is persisted as one new REAL column **`projected_flip`** (idempotent
ALTER, forward-only) so a projected-flip track is possible later. **Restart `options_svc` + the webgui.**
options-scanner **1303 passed / 17 documented-baseline fails**; options_svc **914** (+2 documented
`test_expected_move`); webgui **1046** green. Live-verified: the amber line renders on all four views,
and is correctly absent off-hours when there is no 0-DTE book.
Prior — 2026-08-05 (**Dealer Positioning — "Net Prem" subtab: intraday net options
premium across 28 symbols, in three groups**: a new view on `/options/gamma`, **between Flow and
Term** (they are the two options-FLOW lenses — Flow is one symbol's call/put premium over the
session, Net Prem is the NET of that across many symbols at once — so a reader comparing them
doesn't cross Term). It plots **call$ − put$** as one line per symbol over three groups:
**Indices & Broad** (`$SPX $NDX BIG10 SPY QQQ IWM DIA`), **SPDR Sectors** (all 11 XL*), and
**Mega-caps** (the ten BIG10 names). **The group tab FILTERS the checkbox list; the selection
PERSISTS across tabs** — so any cross-group combination works (`$SPX` beside `XLK`), which is the
whole point: the groups are a way to find 28 checkboxes, not a partition of what may be drawn
together. A **Dollars ($M) / Skew %** picker switches the y-axis, and **both exist because the
magnitudes span four orders**: measured live, **SPY −$375M sat beside DIA +$0.1M** — DIA is a flat
line against SPY's scale — while in skew those same points read **−46.6%** and **+2.5%**, i.e. two
genuinely comparable numbers. Each symbol has a **FIXED colour** (`NET_PREM_COLORS`) that never
changes with the selection, so ticking a box can't recolour the lines you were already reading.
Group, mode and selection persist via `app_settings` (`gamma_netprem_group`/`_mode`/`_symbols`).
**Data path:** PURE `services/options_svc/net_premium.py` (`GROUPS` = the single source of truth for
membership+order, `BASKETS`, `source_symbols()`, `build_series()`; **BIG10 is summed SERVER-side**
from its ten members, dollar-weighted, never passed through from the input) → `compute.build_net_premium`
(DB-only, RTH-cropped, **one reused read-only connection**) → `handlers.publish_net_premium` on the
**existing 1-min GEX branch**, through the **`NetPremiumSnapshot` contract as a real validation gate**
(a shape regression is logged and NOT published, rather than cached and rendered) →
**`cache:options:net_premium`** → the webgui **filters CLIENT-side**, so a checkbox toggle is
instant (no command, no round-trip). **The marginal server cost is near zero** — that branch has
just read these very rows for the Opportunity Board. **The COLLECTION change is where the cost is,
and it is not uniform:** the 11 SPDR sectors were collected by **nothing**, so they (plus `IWM`/`DIA`
and the ten mega-caps, which merely *happen* to already be in the gitignored `Top 20.xlsx`) were
pinned into `gex_collector.SYMBOLS` — **5 → 28 static entries**. Re-measured against the real
workbook: `collection_symbols()` goes **82 → 93, exactly +11 genuinely new symbols** (~**+4,800**
Schwab `/chains` calls/day over the 440-min window); on a **fresh clone with no workbook** it is
**5 → 28 (+23, ~10.1k/day)** — quote BOTH, since which applies depends on a file that isn't in git.
Pinning the non-sector names costs no extra fetches *here* but makes the view independent of that
workbook. **Sector history starts the day this ships** — earlier sessions stay empty and the page
names those symbols as "no data yet" rather than drawing empty lines. **Three knock-on effects of the
wider universe, all intended:** the sectors now also appear on the **Opportunity Board**, in the
**Dealer Positioning symbol dropdown**, and they **FIRE FLOW ALERTS** (UOA + crossover, to
Discord/Telegram) because `handlers._flow_alert_symbols()` derives from the same
`collection_symbols()` — **the user was asked and chose to keep them** ("a large XLE or XLF sweep is
a real rotation signal"); if they prove noisy, **exclude them in `_flow_alert_symbols`, NOT from
`gex_collector.SYMBOLS`** (a note now says so at the cut point, because removing them from collection
would silently empty the view's entire SPDR Sectors group — eleven blank lines, no error). Stale
comment fixed on the way through: `compute._MOVER_INDEX_FLOOR` claimed to "mirror
`gex_collector.SYMBOLS`" (it never did — `$NDX` was already missing), and SYMBOLS now holds ten REAL
mega-caps, so anyone "resyncing" the drift would **silently suppress NVDA/AAPL/TSLA from the
briefing's notable movers**; it is an exclude-list of non-stocks, and the comment now says that.
**The `_set_chart` trap (worth the record):** `flow_figure` and `net_prem_figure` are BOTH
`chart.type == "line"`, so the old **`type`-keyed** recreate check took the in-place merge path — and
Flow's `yAxis` is a **LIST of 3 panel-banded axes** against Net Prem's **single dict**, so Highcharts
merged the dict onto axis 0 and left **two orphaned axes still painted with the plot squeezed into
the top 62%**. Fixed by keying `_set_chart` on **`chart_kind(fig)` = `(chart.type, len(yAxis) if
list else 0)`** — the axis **TOPOLOGY is** the merge surface, so it belongs in the identity; deriving
it from the figure (rather than threading the view name in) means a future view can't regress it by
forgetting an argument; and unlike a view-name key it does **NOT** recreate the element on
GEX→Charm→DEX→Vanna (all `bar`), so the documented no-flicker property is preserved. The series
COUNT is deliberately excluded beyond list-vs-single (ticking a checkbox must not tear down the
element). A **registry-driven test** fails if a view is added without being registered for that
guard. **`net_prem_status_text` separates "not collected yet" from "the publisher is failing"** —
both look identical from Tier 1 (a stale/absent key), and eleven sector lines will legitimately be
empty for a while after ship, so it uses the payload's **own `ts`**: absent → never published;
fresh ts + empty series → fine, nothing collected yet; **ts older than ~2 min while INSIDE the
08:00–15:20 CT window on a trading day** (weekends + NYSE holidays skipped) → the publisher is
failing. Staleness off-hours is deliberately NOT flagged (the key legitimately holds the session's
last tick, as the heatmap and Flow do). It is repainted from the **1 s `_tick`**, not only on
repaints — staleness is a function of the CLOCK, and the one outage it exists to report is exactly
the one that stops every version bump, so a repaint-only line would freeze at "updated 5:20 PM"
forever. **Restart `options_svc` + the webgui.** webgui **1020** + shared/contracts **49** green;
options_svc **912** green **+ the 2 documented pre-existing `test_expected_move` date-relative
failures**; ruff clean. **Verification — stated honestly.** `compute.build_net_premium` was run
against the **live 1.51 GB `gex_history.db`**: 27 series, `BIG10` correctly derived, `XLRE` correctly
omitted (uncollected), SPY net **−$378.4M** at 14:52 CT. The publish path was verified end-to-end
(real compute + real DB + fakeredis), including that injected bogus fields never reach Redis. The
page was browser-verified on :8501 against the real payload by two independent agents — tab position,
default colours, the Flow→Net Prem axis trap, cross-group selection, skew rescaling, `Clear all`, the
"no data yet" note, and console-error parity against an unmodified control build. **NOT yet done:**
the running `options_svc` is from the main repo and **predates this branch**, so
`cache:options:net_premium` is **not being refreshed on a live 1-min cadence yet** — the key was
populated by hand for verification, and full end-to-end needs the service restarted from this branch
after merge. **Also not done:** nobody has clicked the **Scale (Dollars/Skew %)** picker in a
browser — Quasar popups don't render at the automation pane's 0×0 viewport — so it was verified
through the persistence path instead (set the mode, restart, the page comes up in Skew with the right
axis and range); worth one human click before release. Design/plan:
[design](docs/plans/2026-08-05-net-premium-groups-view-design.md) /
[plan](docs/plans/2026-08-05-net-premium-groups-view.md).)
Prior — 2026-08-05 (**Market Dashboard — four frames are now LEADERBOARDS (ranked by
day %-move)**: the **Top 10**, **Sector SPDR**, **Thematic / Industry ETF** and **Countries** frames
on `/market` emit their tiles **ranked descending by day %-change**, so the strongest name sits
top-left. The **BIG10 composite tile is PINNED leftmost** — it carries a `change_pct` of its own (its
members' average), so sorting on value alone would drop it into the middle of the very constituents it
summarizes (live-verified: BIG10 at −0.27% would have sorted 8th of 11). Ranking is **server-side +
pure**: `symbols.SORTED_CATEGORIES` names the four frames and `compute.rank_tiles` sorts each into
three bands — baskets, then quoted tiles by `-change_pct`, then tiles with **no percentage to rank on
(no-data / value-only) LAST** — via a **stable** `sorted`, so equal movers keep their curated
symbol-map order instead of jittering between polls. **Every other frame is deliberately left in
symbol-map order**: Broad-Market ETF reads SPY/DIA/QQQ/IWM then the equal-weights, Volatility reads
VIX then its tenors, Cash Index pairs with Futures — ranking those would destroy a layout that IS the
information (pinned by a test: a big QQQ move must not reshuffle the broad ETFs). **The page mirrors
the rank as a Tailwind flex `order-N` class, NOT by re-inserting DOM nodes** (`market.order_class(i)`;
`order-1..order-12`, then an arbitrary `order-[13]+` so a frame growing past 12 tiles still ranks) —
because the board is built ONCE and updated in place, and re-ordering nodes on the ~2 s tick would
rebuild ~48 tiles every poll and lose exactly that property. The swap removes the **tracked previous**
class (`h["order"]`) rather than a fixed union — order indices are unbounded, so no union can be the
remove-set, and stacked order utilities would freeze the board at its opening rank (the same
tracked-previous idiom as `gamma._set_flex_class`). **Live-verified**: real proxy data ranked all four
frames correctly (Sector XLB +1.56 → XLE −1.78; Thematic XME +4.03 → XSD −2.50; Countries EWC +1.82 →
EWT −0.53) with the unranked frames untouched, and the running app's **bundled Tailwind JIT was probed
in-browser** to confirm it really generates `order-1`/`order-12`/`order-[13]` (the documented JIT
caveat — plain-value arbitraries are safe, only `var()`/`rgba()` ones are not). **Restart `market_svc`
+ the webgui.** market_svc **69** + webgui **973** green; ruff clean. TDD per layer.)
Prior — 2026-08-04 (**Trade Analyzer — "Deep Dive" + "AI Query" buttons (EquityDeepDive
migrated in, 3-tier, NO API)**: two buttons beside **Analyze** on `/trade` run the migrated
**EquityDeepDive** quant engine for the current symbol. **Deep Dive** opens a self-contained
**HTML report** (trend/momentum technicals, fundamentals + short interest, and rich options
analytics — ATM IV, implied move, put/call, max pain, 25Δ skew, IV **term structure**,
constant-maturity 30d IV, net **GEX**/flip, OI walls — plus **IV/RV rank** via a SQLite store) in a
new browser tab; **AI Query** opens a **copyable chat prompt** (the quant digest injected into the
template, HOW-TO stripped) to paste into a chat. **The external `D:\AI_Based_Analysis\EquityDeepDive`
toolkit was migrated into `services/trade_svc/deepdive/`** (`engine.py` ← `equity_deep_dive.py`,
`iv_history.py`, `chat_prompt.py` ← `make_chat_prompt.py`, `chat_query_template.md`, + a pure
`digest.py` extracted from `ai_analyst.py`) — adapted to the **3-tier boundary**: relative imports,
`PROXY_BASE` from `repo_paths.PROXY_URL`, direct-mode/token-file dropped, module-level
`logging.basicConfig` removed, CLI de-guarded. **`ai_analyst.py` (the Anthropic path) is NOT
migrated — there are NO API calls; the "AI note" is a GENERATED QUERY, not an API result** (the
user's explicit constraint). The engine already routed through the app's proxy `/passthrough`
(present) so the fetcher works at `:8100` essentially unchanged. **Wiring (webgui stays engine-free):**
`compute.run_deep_dive`/`build_deep_dive_query` (defensive, never raise) → `deepdive`/`deepdive_query`
commands on `cmd:trade` → `cache:trade:deepdive` (HTML) / `cache:trade:deepdive_query` (markdown) →
`@app.get("/trade/deepdive")` serves the HTML raw + `@app.get("/trade/deepdive-query")` wraps the
prompt in a dark copyable page (read-only `<textarea>` + a `navigator.clipboard` Copy button,
HTML-escaped) — mirroring the `/options/analyze` pattern. The page's two buttons enqueue their command
and a 2 s watcher (`should_open_tab`: pending + version-advanced-past-baseline) opens the tab, so a
page-load with a stale cached result never auto-opens (browser pop-up blocking on the timer-driven
open is the same documented behavior as the Gamma page's `_watch_analyze`). **IV history is on-demand
only** (`repo_paths.IV_HISTORY_DB` = `services/trade_svc/data/iv_history.db`, gitignored; each run
records a snapshot + RV rank; **IV rank shows "building — N of 20"** until snapshots accrue — Schwab
serves no IV history); the scheduled daily job is **deferred**. **No new pip deps** (pandas/numpy/
requests already present; no `anthropic`). **Restart `trade_svc` + the webgui.** webgui **979** +
trade_svc **74** green; ruff clean; **live-verified end-to-end** (real proxy → AAPL/NVDA reports
~10.8 KB, digest-injected query ~8.7 KB, `iv_history.db` created with the snapshot; `/trade/deepdive`
+ `/trade/deepdive-query` serve correctly). Built with brainstorming → plan → per-task TDD. Design/plan:
[design](docs/plans/2026-08-04-equity-deep-dive-trade-button-design.md) /
[plan](docs/plans/2026-08-04-equity-deep-dive-trade-button-plan.md).)
Prior — 2026-07-28 (**Gamma heatmap — spot overlay switchable Line / Candles / OHLC**:
a **Spot** picker on `/options/gamma` draws the heatmap's price overlay as a line (unchanged
default), candles, or OHLC bars, plus a **Bar** picker for the **1/5/15-min** bucket. Both persist
via `app_settings` (`gamma_spot_style` / `gamma_spot_interval`); the bar picker is HIDDEN for the
line, where it would mean nothing. **Spot is stored as a 1-min POINT SAMPLE, not a bar**, so
`gamma.ohlc_bars(spots, interval)` derives O/H/L/C the way any tool builds bars from a sampled
series: **open = the PREVIOUS bar's close** (carried forward, so bars are contiguous and even a
1-min bar has a body — that minute's move — instead of a degenerate `O==H==L==C` dash), high/low
spanning that open plus the bucket's samples, `x` = the bucket's CENTRE column so the bar sits over
the cells it summarizes. **HONEST LIMIT (in the control's tooltip): highs/lows are sampled once a
minute, so wicks understate the true intra-minute range** — these are bars over the same series the
line draws, NOT exchange bars; true wicks would need real intraday bars fetched from Schwab.
**They are deliberately NOT Highcharts `candlestick`/`ohlc` series** — those are STOCK types, and
loading that module BREAKS this chart outright (zero series, live-verified even with the style on
Line; see the expanded stock-module gotcha in "NiceGUI gotchas" — the 2026-07-06 note blamed
`type="stockChart"`, but it is the MODULE). Instead `gamma.candle_points(bars)` emits two **CORE**
series — a `columnrange` **body** (open→close) + an `errorbar` **wick** (low→high) — each POINT
carrying its own color (`UP_COLOR` green / `DOWN_COLOR` red) so ONE series holds both up and down
bars; `columnrange`/`errorbar` need **no `extras`** (the `more` module auto-loads), so nothing
patches `update`. **OHLC is the same geometry drawn thin** (`pointWidth: 2` + whiskers) so it reads
as a bar — true left/right open-close ticks are unreachable without the stock module. Series count
stays **fixed at 9** (its regression guard updated 7 → 9). **Restart the webgui.** Live-verified on
390 real rows: 78 bars at 5 min, **48 green / 30 red** matching the computed directions, wicks
drawn, heatmap + level lines intact, bars survive a GEX→Vanna→GEX **in-place** update (the same path
the picker uses), and OHLC renders 2px wide vs full-width candles. webgui **975** green.
Prior — 2026-07-28 (**Nav — Calculator + Simulator paired under a new "Strategy Tools" group**:
a webgui NAV-ONLY change. The two MODELLING tools are the app's most tightly coupled pages — they share
`leg_editor.py` / `strategies.py` / `strategy_menu.py` / `page_state.py` and each has a **Copy to the
other** button — yet they straddled two nav levels (**Calculator** a standalone `OPTIONS_RAIL` page with no
tab strip, **Simulator** an `OPTIONS_CHILDREN` tab), so the copy button threw you between them. They are now
one drawer item with two tabs: new **`STRATEGY_TOOLS_CHILDREN`** (Calculator · Simulator) + a
`("Strategy Tools", "build", …)` entry **APPENDED** to `_NAV_GROUPS` (appended, not inserted, so the
positional `_NAV_GROUPS[0..2]` reads in `_layout` stay valid; both consumers ITERATE, so lookup order is
irrelevant). The drawer renders it via `_NAV_GROUPS[3]` **immediately after the Options group** — exactly
where the Calculator rail item used to sit. Breadcrumbs change accordingly
(`/options/calculator` → "Strategy Tools · Calculator", was "Calculator · "). **Deliberately NOT Options
tabs:** that strip is the find → analyze → track → repair workflow over signals the app FINDS, whereas these
two model legs you bring yourself. **Options strip 8 → 7 tabs; `OPTIONS_RAIL` 3 → 2** (Dealer Positioning ·
Opportunity Board); drawer items stay **9** (4 groups + 2 rail + 3 flat), and the icon-distinctness guard
still holds (`build` is new). **NOTHING ELSE CHANGED — routes, page modules, `_TAB_COLOR` (keyed by ROUTE,
so favicon colors are unchanged), and `page_help` (also keyed by route, so both pages keep their guides)
are untouched**; `_NAV_LABEL` gained the new list so tab titles + the 2 s hover guides follow automatically.
TDD — the nav lists are pure data, so the `test_shell.py` assertions ARE the spec (red first, then green);
the drawer-reachability test asserts one `_nav_group_link` call per `_NAV_GROUPS` entry, so a future group
added to the data but never rendered fails loudly. **Restart the webgui.** webgui **951** green;
live-verified (drawer `build` icon under Options, tab strip "Calculator · Simulator", breadcrumb "Strategy
Tools · Calculator", Simulator gone from the Options strip, no console errors). Prior — 2026-07-28 (**Gamma heatmap — RTH-only window, level lines across the plot, and an
optional intraday level-movement track**: three display-side changes to `/options/gamma`
(commits `7533599` / `8b405ec` / `a741647`). **COLLECTION IS UNCHANGED throughout** — the GEX poll still
runs 08:00–15:20 CT and stores every snapshot; all of this is what the page *draws*.
**(1) RTH-only time axis.** The strike×time heatmap (GEX/Charm/DEX/Vanna) **and the Flow chart** now plot
only **08:30–15:00 CT**. The ~48 pre/post-market columns are near-flat (the index barely ticks pre-open and
OI is static), so they stretched the session without informing it — measured live: **431 collected rows →
383 displayed**. New in `compute.py`: `_RTH_START`/`_RTH_END` (08:30/15:00 CT, matching
`market_svc/scheduler`'s existing definition), `_rth_bounds(session_date)` (computed ONCE per snapshot and
compared numerically — far cheaper than converting every row's ts), `_rth_only(rows, bounds)` (inclusive
both ends; **`bounds=None` → passthrough**, so a failure shows everything rather than silently blanking the
chart), and **`_display_session_date(now, session_date)`** — because `scheduler.active_session_date` flips to
today at the **08:00 COLLECTION** start, today has rows but none *displayable* until 08:30, so this keeps
showing the **prior session** across that gap (the charts are never blank mid-morning). Filtering happens
**AFTER** `_history_rows_incremental`, so the append-only memo still keys off the true last-collected ts.
**(2) Flip + call/put wall lines across the heatmap.** `gamma.wall_plot_lines(spot, walls, flip)` emits them
as **yAxis plotLines** — horizontal, so they span the full time axis — labeled/colored to match
`line_annotations` (FLIP_COLOR blue / WALL_COLOR purple) so the bar panel and the heatmap read as one.
**Spot is deliberately NOT added**: the heatmap already carries it as a *moving* line series, which is
strictly more informative. **The `plotLines` key is ALWAYS emitted (empty when there are no levels)** —
in-place `chart.update()` MERGES options, so omitting it leaves the previous view's lines painted over the
new one (live-verified: GEX→Vanna now replaces them). **(3) "Level movement" overlay (NEW, toggleable).**
A `ui.switch` beside *Refresh now* overlays where the flip + walls sat at **every snapshot** of the session
— so you can watch a wall build, migrate or give way instead of inferring it. **Off by default**, persisted
via `app_settings.gamma_level_tracks`. **No new collection and no schema change** — `gex_history` already
stores a per-strike grid per snapshot, so the history is complete the moment it ships. **The walls are
RECOMPUTED per row** (`compute._level_track(rows, vname)`) from each snapshot's own grid via
`gt.get_directional_walls`, **NOT** read from the stored `top_pos_strike`/`top_neg_strike` columns: those are
a DIFFERENT metric (max/min **net** strike anywhere in the chain vs the largest call **above** spot /
most-negative put **below** spot that the chart draws) and **disagreed on 383 of 383 rows** of a live
session — reusing them would draw tracks contradicting the wall lines beside them. Costs ~11 ms/view/session.
`_level_track` MUST run **BEFORE `_crop_gamma_views`** (a wall can sit outside the ±N-strike display window,
which the crop would truncate). Flip comes from the stored column (already the right definition); walls are
**GEX/DEX only**, mirroring `gamma_walls` — Charm/Vanna track the flip alone. Rendering
(`gamma.track_points` + `heatmap_figure(levels=, show_tracks=)`): **step lines** (a wall holds a strike then
JUMPS — interpolation would draw levels that never existed), **nulls kept as gaps** (skipping them would
shift every later point a column left and mis-date the movement), **solid** against the **dashed** static
lines (dashed = the level now, solid = how it got there — the user chose to keep BOTH), and the 3 track
series are **ALWAYS emitted (empty when off)** so the series count stays **fixed at 7** — a varying count
makes Highcharts replace rather than update series, shifting colorIndex and leaving stray paths (the
documented bug; its regression guard was updated 4 → 7). **Restart `options_svc` + the webgui.**
Live-verified: heatmap + Flow axes start 08:30; today's GEX flip travelled **7384.61 → 7421.05** with the
call wall stepping 7430/7435/7440/7450/7460; tracks survive a reload with the toggle persisted; no new
console errors. options_svc **871** (+2 documented `test_expected_move` baseline fails) / webgui **948**
green. **Gamma test fixtures** carried ordinal timestamps (`ts=1`, the 1970 epoch); now that the code filters
on time they are rebased onto the pinned session's RTH open, preserving ordering + the `ts > since_ts`
incremental semantics. Prior — 2026-07-27 (**App-wide Symbol-field UX — uppercase / Tab+Enter load / click-to-select**:
every Symbol entry field now behaves identically, driven by the shared `webgui/pages/options/inputs.py`
helpers. **(1) All caps** — `select_all_on_focus` now also adds the `uppercase` class (`text-transform`,
applied ONLY to the Symbol field; the load handlers still read `value.upper()`, which owns correctness).
**(2) Tab/Enter = load** — a NEW `bind_symbol_load(inp, load, *, tab=True)` fires the page's load/fetch/scan
on **Enter** and on **focusout** (tab/click-out), deduped via `should_load` (seeded from the initial value,
so a default `SPY` doesn't auto-load on first blur; the Load BUTTON still force-loads). **(3) Click-to-
highlight** — `select_all_on_focus` now DEFERS its `select()` one tick (`setTimeout`), because on a mouse
click the browser's mouseup drops the caret and clears a synchronous selection — the old immediate select
only survived tab-in, not a click. **Wiring**: Calc/Sim/Trade already had Enter+focusout+dedup (uppercase
+ the click fix come free via `select_all_on_focus`); **Swing** + **Rescue** gained focusout via
`bind_symbol_load`; **Expected Move** gained Enter→Draw on Symbol+Expiry (NO focusout — it's a multi-field
form needing an expiry, so tabbing symbol→expiry must not submit); **Gamma**'s select-with-input gained
uppercase + focus-select (it already loads on change). **Restart the webgui.** webgui **939** green (+2
inputs tests); ruff clean; live-verified with real keystrokes (Calc: type "aapl"→AAPL chain on Tab; Swing:
"amd"→AMD scan on Tab, overwrote SPY on click, displays caps). Prior — 2026-07-27 (**Simulator slider text → plain language, symbols dropped (all sub-tabs)**:
a webgui-only copy rewrite of `webgui/pages/options/simulator.py`'s user-facing text so the Replay /
What-if / IV-shock controls read in plain English with no math notation. Slider labels + tooltips:
`ΔS 0%`→**"Price change: 0%"**, `Δt 5d elapsed`→**"Days passed: 5"**, `IV ×1.5`→**"Volatility multiplier:
1.5"**, `Cursor —`→**"Drag the slider to step through time"** (cursor readout `Cursor {ts}`→"Showing
{ts}"). Chart text: What-if axes `Underlying`/`P / L`→**"Underlying price"/"Profit / loss"** + tooltip
`S {x} → P/L …`→**"Price {x}: profit / loss …"** (drops the `→`/`S`); IV-shock legend `base (×1.0)`/
`shock (×N)`→**"Current volatility"/"Volatility multiplied by N"**, category `Gamma×100`→**"Gamma (times
100)"**; Replay look-back menu `1-min · 1d`→**"1-minute bars, 1 day"** (etc.). Trader terms kept
(Delta/Gamma/Theta/Vega/Rho, "IV"). No logic/figure-structure change — pure labels; the earlier RRG
quadrant-label half of this request already shipped. **Restart the webgui.** webgui test_options_simulator
**22** green; ruff clean; live figure-text verified symbol-free. Prior — 2026-07-27 (**IV Rank column on the scanner/finder tables**: added an **IV Rank**
column to all four opportunity tables — **Market Scanner** 0-DTE / Swing / Directional and the **Strategy
Finder** — so the dealer-cheap/rich IV context sits beside each candidate's score. **Data path**: the
per-symbol `iv_rank` already lives in `run_iv_analysis` output. In `scanner_engine.run_full_scan` the
iv_rank injection was **hoisted out of the `signal_recorder` try** (so an import failure there can't strip
the column) and **extended to `signals_directional`** (previously only 0-DTE/Swing got it; missing IV → 0
sentinel, unchanged); in `options_svc.compute.swing_scan` each candidate is stamped with the single
symbol's `iv_rank` (None when the IV analysis can't compute a rank). **Display**: `scanner.signal_columns`/
`signal_rows` + the shared `strategy_table.strategy_columns`/`strategy_rows` (which the Directional tab and
the Strategy Finder both render) gained an `("iv_rank","IV Rank")` column placed before Score; the cell is
`scanner.iv_rank_value` — the rank rounded to a whole number (numerically sortable) or blank when
missing/non-numeric. Additive/back-compat (freeform signal dicts, `_DAY_STRIP` doesn't touch it, so the day
union carries it through). **Restart `options_svc` + the webgui** — cached OLD-engine signals have no
iv_rank until the next scan republishes. Live-verified end-to-end against the proxy (SPY swing 35 signals
all iv_rank 65.2; directional SPY 65.2 / QQQ 71.8, all 16 carry it). webgui **903** + options_svc
test_compute/handlers **331** green (+ the 2 documented `test_expected_move` baseline fails elsewhere);
options-scanner scanner_engine/signal_recorder green (bar the 2 pre-existing `TestEarningsAvoidance`
stale-fixture fails); ruff clean. Prior — 2026-07-27 (**Rebrand → NeuralStrike**: the app is renamed from "Schwab Trading" to
**NeuralStrike** and the supplied logo is in the header. **Header lockup** = the **NS monogram** + a
**two-tone wordmark** ("Neural" gold / "Strike" blue, **Montserrat ExtraBold**, uppercase) — replacing the
old blue-gradient tile + chart-glyph SVG (`.brand-tile` deleted, dead). The gradient stops are **SAMPLED
from the artwork** (p50→p95 of each wordmark band — the low percentiles are anti-aliasing against the
black background and read too dark), so the header MATCHES the logo instead of approximating it:
`#C9A356→#FBEAA0` gold, `#2C6FB4→#35A3F5` blue. **Assets** in a new **`webgui/static/img/`** (already
served at `/static`): `neuralstrike-logo.jpg` (the supplied full lockup — brand source of truth) +
**`neuralstrike-mark.png`** (a 256px SQUARE crop of the monogram ALONE, computed from the artwork's content
bbox ABOVE the wordmark band — the full 832×1248 portrait lockup would be an unreadable smudge in the 28px
header tile AND its own wordmark would compete with the rendered one; regenerate it from the .jpg). **All
of it is config-driven** via a NEW **`[brand]`** block in `config/theme.toml` (`name_a`/`name_b`/
`font_family`/`font_url`/`font_weight`/the 4 gradient stops/`mark`) → `theme.BRAND_NAME`/`BRAND_CSS`/
`BRAND_FONT_HEAD_HTML` + `main.brand_lockup_html()`/`brand_mark_src()`, so renaming or restyling the app is
a **config edit, not a code hunt** (the browser tab title, `ui.run` title, and breadcrumb fallback all
derive from `theme.BRAND_NAME`; a test asserts no "Schwab Trading" survives in `main.py`). **The brand font
is deliberately WORDMARK-ONLY** — loaded via its own `[brand].font_url` link, SEPARATE from
`[typography].font_url`, because the body/data font must stay IBM Plex: a heavy display face hurts
readability in the dense signal tables. **⚠ Montserrat ExtraBold is a CLOSE FREE MATCH for the logo's
typeface, not a positive identification** (the exact face can't be read off a raster image) — swapping it
is two config lines. The wordmark CSS is **RAW CSS, not Tailwind** (it needs `linear-gradient` +
`background-clip:text`, which the bundled JIT won't reliably emit — the documented rgba/gradient-arbitrary
trap). **Degrades safely**: a missing mark file renders the wordmark ALONE (never a broken-image icon,
enforced by a file-exists check + test), a blank `font_url` falls back down the local stack, and
`[brand].mark = ""` disables the image. **`[brand]` is deliberately NOT in Settings → Appearance** —
`_THEME_SECTIONS` tags each section single-kind (all-color or all-text) and `[brand]` mixes both. Launchers
renamed too (`start_all.bat`/`start_all_wt.bat`/`stop_all.bat` window titles + banners). **UNCHANGED by
design**: the repo folder name + every path (renaming it would break `repo_paths`, the launchers, and the
venv), the **per-route favicon colors** (each page keeps its own colored square so several open tabs stay
tellable apart — a single logo favicon would make every tab identical), the Deep Slate palette everywhere
else, and `webgui/static/sounds/`. **Restart the webgui.** webgui **888** green (6 new), ruff clean;
live-verified (Montserrat 800 genuinely loads + applies — measured 130.8px vs 120.9px fallback — both
gradients clip to text, the mark serves at 28px, no console errors). Design:
[design](docs/plans/2026-07-27-neuralstrike-rebrand-design.md). Prior — 2026-07-27 (**Menu reorganization — rail promotions, plain-language renames, workflow
ordering**: a webgui NAV-ONLY change (`webgui/main.py`'s five nav lists + the text that names them).
**(1) Rail promotions.** **Calculator** and **Gamma** moved OUT of the Options tab strip into
`main.OPTIONS_RAIL` as standalone main-menu pages, joining Matrix — rail order **Calculator · Dealer
Positioning · Opportunity Board**. Like Matrix, a rail page has **NO tab strip** and its breadcrumb is
just the page name (`_group_children` → None). **(2) Seven renames** (menu labels, breadcrumbs, browser
tab titles, page-help headings): `Gamma`→**Dealer Positioning** (says what it shows — where dealers must
hedge), `Scanner`→**Market Scanner** + `Swing Scanner`→**Strategy Finder** (the Scanner has its OWN Swing
subtab, so "Swing Scanner" pointed at the wrong page; `/options/swing` is a multi-strategy single-symbol
scanner, not a swing-specific one), `Paper Trades`→**Paper Ledger** + `Paper Portfolio`→**Paper Account**
(they read DIFFERENT databases — `trades.db` ledger vs `paper_account.db` engine account, which settle
expiration differently — and the old labels gave no way to tell which one a "Send to Paper" button wrote
to; both page guides now state the distinction explicitly), `Matrix`→**Opportunity Board**,
`Terminate`→**Stop All Services**. **(3) Options tabs reordered** find → analyze → track → repair
(Market Scanner · Strategy Finder · Simulator · Expected Move · Captured Signals · Paper Ledger · Paper
Account · Rescue) — the two finding tools had been separated by three tracking pages. **(4) Market
Dashboard folded into the Market Trend & Sentiment group** as its FIRST tab (it was a flat rail item);
since `_nav_group_link` navigates to `children[0]`, **that group's rail item now lands on `/market`**.
Drawer items **8 → 9**. **NOTHING ELSE CHANGED — routes, cache keys, commands, page modules
(`gamma.py`/`swing.py`/`paper.py`/`portfolio.py`), and the financial vocabulary are untouched**: "gamma
flip"/GEX/the Charm-DEX-Vanna-Flow-Term view names, the Greek Delta/**Gamma**/Theta/Vega/Rho lists, and
the **Gamma Explain / Gamma Analysis** report documents (whose titles are also the persisted briefing
history's identity) keep their names by design. Because routes are unchanged, every cross-page handoff
(Send to Calculator, Expected Move, `/options/explain`, `/options/analyze`, the briefing-history links)
works untouched. `_NAV_LABEL` is derived from the lists so tab titles + the 2 s hover guides follow
automatically; `_TAB_COLOR` is keyed by ROUTE so the favicon colors are unchanged. **Stale docs fixed on
the way through**: the `MORE_CHILDREN` comment claiming Settings renders as an indented sub-group (retired
with the expandable drawer), `page_help.py`'s docstring pointing at the removed header "?" button, and the
User Guide's navigation table (which still described expandable groups, a two-page Sentiment group, and a
drawer that remembers open groups). **Restart the webgui.** TDD — the nav lists are pure data, so the
`test_shell.py` assertions ARE the spec: red first (3 failures), then green. webgui **861** green, ruff
clean; live-verified. Design/plan:
[design](docs/plans/2026-07-27-menu-reorganization-design.md) /
[plan](docs/plans/2026-07-27-menu-reorganization-plan.md). Prior — 2026-07-25 (**Per-category notification routing — move a channel without a code
change**: every Discord webhook AND Telegram chat is now configurable **per notification category**
via a new **`routes`** block in gitignored `shared/notifications.json` (+ blank placeholders in
`.example.json`). **9 categories**: `signals` (scanner + captured merged) · `flow_uoa` ·
`flow_crossover` · `flow_gamma_flip` · `action_alert` · `eod_summary` · `gamma_briefing` ·
`market_snapshot` · `market_state`. Each is `{"discord": "", "telegram_chat_id": 0}` — **blank/0/missing
= inherit the global** (`discord.webhook_url` / `telegram.chat_id`), so you fill in only what you want
split out. **Why:** four categories (signals, action_alert, eod_summary, market_state) were HARDCODED to
the global webhook, the existing overrides lived in **three inconsistent shapes**, and **Telegram had no
per-category routing at all**. Two PURE resolvers in **`shared/notify/channels.py`** —
**`discord_target(cfg, category) -> str`** and **`telegram_target(cfg, category) -> (bot_token,
chat_id)`** (the **bot token stays global**; only the chat moves) — resolve **`routes.<category>` → the
category's LEGACY key → the global**, first non-empty wins, where "unset" means `None`/`""`/`0` (so a
blank template placeholder can never shadow the global). **The LEGACY step is the back-compat
guarantee**: `discord.flow_{uoa,crossover,gamma_flip}_webhook_url`, `discord.market_snapshot_webhook_url`
and — the odd one out, in its OWN block — `gamma_briefing.webhook_url` all still win over the global, so
**existing installs behave identically with zero config edits** (live-verified: with `routes` all blank,
all 9 categories resolve exactly as before). Shared by BOTH services, so `sentiment_svc`'s
`state_alert.send_state_transition` (previously hardcoded) is routable too. The ad-hoc
`push_notify.flow_webhook()` / `_ms_webhook()` helpers were **deleted** in favor of the resolver (+ a tiny
`flow_category(a)` type→category mapper; an **unknown** flow type falls through to the GLOBAL webhook, NOT
the signals feed — pinned by a test). **SMS is deliberately NOT routed** (single number) and there are **no
per-route env vars** (18 new names for little gain; the existing `DISCORD_WEBHOOK_URL`/`TELEGRAM_CHAT_ID`
env overrides still act on the globals). **To move a channel: edit `routes.<category>` in
`shared/notifications.json`, then restart `options_svc` (+ `sentiment_svc` for `market_state`)**. Green:
shared/notify **56**, push_notify **120** (was 105), state_alert **14**, sentiment_svc **189**, full
options_svc **863** (+ the 2 documented `test_expected_move` date-relative baseline fails); ruff clean.
Built subagent-by-subagent (TDD per unit). Design/plan:
[design](docs/plans/2026-07-25-per-category-notification-routing-design.md) /
[plan](docs/plans/2026-07-25-per-category-notification-routing-plan.md). Prior — 2026-07-25 (**`gamma_tool.py` split — legacy Tk window parked, engine goes headless**:
`options-scanner/gamma_tool.py` was **7,203 lines, 43% of it a dead Tk GUI** — `class
GammaWindow(tk.Toplevel)` (lines 4068→EOF) — with `import tkinter` / `import matplotlib` /
**`matplotlib.use("TkAgg")` at MODULE scope**. Every headless importer of the engine paid for it:
`services/options_svc/compute.py` (~10 lazy `import gamma_tool` sites), `gex_collector.py`,
`scanner_engine.py`, `tools/gex_term_one_shot.py` — **measured 0.69 s per import, pulling
tkinter + matplotlib + PIL into server processes** and forcing the TkAgg backend process-wide.
The window moved to **`options-scanner/gamma_window_legacy.py`** (parked, nothing constructs it —
its `dashboard.py` entrypoint was never copied into this monorepo). **Result: 7,203 → 4,105 lines,
import 0.69 s → 0.207 s, `sys.modules` 478 → 239, no GUI toolkit in any service.**
**Three pure engine helpers were buried in the GUI class and had to be lifted out first** (each had
tests, so each was a real API): `_calc_flip_point` → module-level **`calc_flip_point`** (pure engine
code in `build_analysis_dict` had been reaching *forward* into the Tk class), and
`_fetch_symbol_analysis_impl` → **`fetch_symbol_analysis`** (chain fetch + 4-view
`build_analysis_dict`). The third, `_fetch_last_close`, is **GUI-only** (used solely by the window's
render path) so it stayed with the window and `tests/test_heatmap.py` was repointed — it had been
locating the class by *reflectively scanning `gamma_tool`'s namespace*, which is why moving the class
broke it. `build_chart_style_vars` (needs a live Tk root) now imports `tkinter` **function-locally**;
`draw_term_heatmap` already imported matplotlib locally. **`tests/test_gamma_tool_headless.py` pins
this** with a **subprocess** import probe — an in-process `sys.modules` check is useless here because
the rest of the suite imports tkinter for the legacy dashboard tests. Green: options-scanner
**1286 passed / 17 failed** (baseline was 1283/16/1skip + my 3 new tests; the one moved failure is a
`test_dashboard_*` case that previously *skipped* — all four fail `ModuleNotFoundError: No module
named 'dashboard'` in isolation, pre-existing), options_svc **848 passed / 2 failed** (exactly the
documented `test_expected_move` date-relative baseline); ruff clean. **No service restart needed** —
behaviour is unchanged, this is import-graph surgery only.
**The DB half of the same audit also shipped** (`02d4833`): (1) **grid floats are rounded to 6
significant figures** before zlib in `gex_history_db._encode_grid` — measured on live rows, grid size
is driven by float ENTROPY not strike count (all views ~114 strikes, but vanna serialized values like
`1.2345678901234e-05`), so this cuts the payload **966 MB → 691 MB (29%)**, forward-only, and
flip/walls are unaffected (own columns, computed from the full chain). **An insert-time strike crop
was deliberately REJECTED**: the read-time `_crop_gamma_views` widens its window to span the intraday
spot PATH, and at write time the session's range is unknown — over 239 symbol-sessions a ±20 crop
holes the heatmap on 7, incl. **every `$NDX` session** (42–72 strikes of drift; a 2% day on a 28,000
index with 10-wide strikes is ~56 strikes). (2) **`purge_keep_sessions` now runs a bounded
`PRAGMA incremental_vacuum(20000)`** after a non-empty delete (~80 MB/day, short lock, best-effort,
no-op without incremental auto-vacuum) — nothing in the repo had EVER called it, so retention only
ever moved pages to the freelist. **Gotcha worth knowing:** the live DB *reported* `auto_vacuum=2`
but `incremental_vacuum` reclaimed exactly **1 page** — setting that pragma on an existing DB updates
the header WITHOUT building the pointer-map pages it needs, so the mode was nominally on and
functionally dead. The one-time `tools/vacuum_gex.py` full VACUUM (weekend, services down) fixed it:
**2.06 → 1.07 GiB, 1,007 MB reclaimed in 27 s**, freelist now 0 and ptrmap built, so the new bounded
reclaim actually works going forward. A stale **3.04 GB `gex_history.db.bak`** (Jun 26–Jul 1, disjoint
from live) was also deleted. **~4 GB reclaimed total.** `options_svc` picks up the new write path on
next start. Still open from the audit: splitting `options_svc/compute.py` (6,243 lines) — blocked on a
concurrent session's unlanded `market_premium_aggregate` pairing, see `f0861af`. Full findings:
[plan](docs/plans/2026-07-25-module-split-and-db-efficiency-plan.md). Prior — 2026-07-25 (**EOD retrospective briefing + live macro news + notable movers**:
the 4×/day Gamma briefings were reworked. **(1) The `close` slot moved 14:58 → 15:15 CT and became a
RETROSPECTIVE.** It used to fire **two minutes before the cash close** and write an intraday playbook
for a session with no session left. `scheduler._ANALYZE_SLOTS["close"] = (15, 15)` and
`handlers.run_scheduled_gamma_analyze` now routes that slot to a new **`compute.eod_briefing()`** —
its own forced **`submit_eod`** tool, its own past-tense system prompt (explicitly forbidden from
advising entries into a session that has ended), and its own renderer **`eod_infographic_html`** whose
per-index card shows a **`recap`** and deliberately **drops `what_if`/`close_outlook`** (pinned by a
test). It returns the same `{"html","analysis"}` shape, so caching / history / the existing PNG push
are unchanged. The document is titled **"End-of-Day Recap"** (`_analyze_doc` gained an optional
`title`; the intraday default is untouched) — the same misnaming trap the Market Snapshot push had to
correct. **(2) All four briefings gained macro drivers + notable movers.** `_notable_movers`
(code-computed from `cache:options:matrix` + `cache:market:dashboard` + today's flow alerts) and
`_research_news` (a **phase-1 Claude call carrying the web-search server tool** — separate from the
render phase because the API cannot fire a server-side search AND force a client tool in one turn)
feed both paths; `_ANALYZE_TOOL` gained **optional** `macro_drivers`/`movers` (NOT required, so a
terser reply still parses) and the shared `_movers_html`/`_macro_html` sections render nothing when
absent. **(3) `_eod_session_recap`** derives each index's open/high/low/close from the cheap flow
series (no grid decode) and pairs it with the closing flip/walls into plain-English level verdicts
("reclaimed the gamma flip, closing above" / "lost it and closed below") the model must copy verbatim.
**Code-authoritative throughout** — EM, movers and macro_drivers all override the model's
transcription, matching the existing EM rule. **FOUR bugs that ONLY live verification caught** (every
unit test and two full-suite runs were green): (1) a **self-inflicted regression** — adding
`macro_drivers`+`movers` pushed the intraday reply past `_ANALYZE_MAX_TOKENS=1500`, which stopped at
*exactly* 1500 and truncated **`indices` to n=0**, silently costing the briefing every ladder, tile,
`what_if` and `close_outlook` while still rendering a valid-looking page (both budgets → **2600**, a
cap not a spend, with a `>=2400` tripwire test and a `max_tokens` **warning log** at both call sites);
(2) `next_session` was optional so the model omitted it and the "prepare for tomorrow" block silently
vanished (now `required`, all four sub-fields); (3) even when required, the model **drops `indices`
about one run in three** (`stop_reason: tool_use`, ~1460 tokens — satisficing, not truncation; the API
does not hard-enforce `required`), so **`_backfill_indices` rebuilds the cards deterministically** from
the levels/path/EM the app already computed, synthesizing a factual recap sentence — only the model's
prose is lost, and it logs when it fires; (4) `gex_history_db.latest_flip` is
**`(conn, symbol, view="gex", date=None)`** — passing the date positionally lands it in `view` and
returns None for every symbol (keyword + regression test). Also fixed: `_eod_cache_reads` built a fresh
`Bus()` per call (each one opens a connection, and under pytest a whole new in-memory fakeredis server,
~1.2 s) → a lazily-created **module-level handle**. **Restart `options_svc`** (+ the webgui for the
relabelled "EOD recap" Auto-briefings / History entries). Cost: ~8 Claude calls + 4 web searches/day for
briefings. Green: options_svc **848** (+ the 2 documented `test_expected_move` baseline fails), webgui
**885**. **Live-verified end-to-end**: real web-search drivers (tariffs / Apple / Iran-oil), real movers
(NBIS −14.1%, ALAB −11.7%, CRWV −11.1%), correct level verdicts off Friday's real session (QQQ −1.12%
lost its flip; SPY reclaimed 738.49), code-authoritative EM, three complete runs, and a **real PNG push
delivered to Telegram + Discord**. Design/plan:
[design](docs/plans/2026-07-24-eod-briefing-news-movers-design.md) /
[plan](docs/plans/2026-07-24-eod-briefing-news-movers-plan.md). Prior — 2026-07-24 (**Market Snapshot push — 30-min Discord/Telegram infographic**:
a new scheduled push (in **`options_svc`**) fires **every :00 and :30 during the trading day**
(08:30–15:00 CT, ~14/day) delivering **one server-composed PNG** to Telegram + Discord — the **Market
Dashboard tile-grid** (all ~14 categories, risk-on/off colored, from `cache:market:dashboard`) plus a
**"Market Read"** section of three **gauge + intraday-sparkline** panels with static explainer + live
read: **Daily Market Trend** (`cache:sentiment:composite`→`derived.trend`), **Daily Market Sentiment**
(→`live.composite`), **Daily Market Regime** (`cache:sentiment:regime` + `regime_history` membership
mix). **Zero new deps, no Claude cost.** Reuses the existing rails: pure builder module
`services/options_svc/market_snapshot.py` (inline-SVG gauge/sparkline/regime-mix + dashboard HTML +
`market_snapshot_doc` — a self-contained dark doc reusing `compute._ANALYZE_CSS` via a LOCAL wrapper so
it's titled "Market Snapshot", NOT "Gamma Analysis") → `briefing_image.render_html_png` (headless
Chrome) → `push_notify.send_market_snapshot` (mirrors `send_gamma_briefing`: master `enabled` +
`market_snapshot.enabled` gates, Discord routes to `discord.market_snapshot_webhook_url` →
`webhook_url`, text fallback on render failure, size guard, no SMS). Scheduled via
`scheduler.market_snapshot_due(now, ran_slots)` (constants `_MKT_SNAP_START/_END`, :00/:30 slots,
10-min grace, trading-day gated) wired into `loop()` (non-blocking `launch_branches`, latch-before-work,
mirrors the action-alert branch); `handlers.run_market_snapshot(bus, slot)` reads the six caches
(**unwrapping `CacheEnvelope.payload`**), pushes, and caches inputs at **`cache:options:market_snapshot`**.
Config: a `market_snapshot` block in gitignored `shared/notifications.json` (`enabled` + the real
webhook) + placeholder in `.example.json`; the **window is scheduler constants, NOT config** (start/end
keys were dropped as a false affordance). **Two bugs only LIVE verification caught** (all unit tests +
two code reviews passed): (1) `sentiment_svc` publishes `total_score` as a formatted **STRING**
(`"6.70"`), so an `isinstance(...,(int,float))` guard left the Sentiment gauge/caption dead → fixed with
tolerant `float()` coercion; (2) `bus.cache_get` returns a **`CacheEnvelope`**, not a dict — the handler
called `.get()` on the envelope → caught by its best-effort guard → the push **never fired** → fixed by
unwrapping `.payload` (the unit test's fake bus now returns `SimpleNamespace(payload=…)` to match the
real contract). **Restart `options_svc`** to schedule it. Built subagent-by-subagent (TDD, spec + quality
review per unit + a final holistic integration review); green: market_snapshot **19**, push_notify **+3**,
scheduler **+5**, handlers **+2**; full options_svc suite 789 pass (the 2 documented `test_expected_move`
date-relative baseline fails aside); ruff clean. Live-verified end-to-end (real caches → PNG rendered +
visually confirmed → real test push returned True). Design/plan:
[design](docs/plans/2026-07-24-market-snapshot-push-design.md) /
[plan](docs/plans/2026-07-24-market-snapshot-push-plan.md). Prior — 2026-07-23 (**Market Regime — blended structural classifier (Phase 1, CONTEXT-ONLY)**:
a THIRD classification axis alongside the direction × aggression five-state — **market STRUCTURE**
(*how* the tape is moving): **Mean Reversion / Trending / Breakout / Choppy / Volatile**
(the fifth regime was renamed **Crisis → Volatile** on 2026-07-24 — "Crisis" overstated what it
detects; the internal membership key stays `crisis`). Its **primary tell is now the absolute VIX level**
(`ramp(VIX, 22, 34)`); the ATR-percentile floor was raised 0.85→0.92 so a merely-wide day no longer
reads ~34% — it now needs genuinely elevated implied/realized vol. Built
**soft-first**: the primary output is a **membership VECTOR** (each regime a continuous 0-1 weight),
so a regime handover reads as a **gradual band shift + an explicit transition** ("Mean Reversion →
Trending · 60%") instead of a threshold flip; the hard label is derived for display only and lags
behind per-challenger hysteresis. **Pure core** (`sentiment-dashboard/scoring/`): `volatility.py`
(Wilder ATR + Bollinger width + percentile), `market_regime.py` (20-key optional evidence contract →
five per-regime scorers — weighted-average / **multiplicative** breakout / **max()** crisis — →
normalized memberships + `confidence = max(raw)` + an honest **"Unclear"** floor; then the temporal
layer: **wall-clock half-life** EMAs (fast 15 min / slow 60 min, so cadence and smoothing are
independent knobs), `detect_transition`, challenger-aware `commit_label`, and `apply_crisis_attack`),
`regime_evidence.py` (bar-derived assembly reusing `technical`/`profile_shape`/`volatility`; the
signed `session_structure`/`rejection_defense` scorers were deliberately NOT reused — their blended
output can't express a both-sided magnitude or an enum). **Service**: `compute.compute_market_regime`
(TTL-memoized multi-session SPY 5-min fetch → today's session sliced out for the session-scoped
evidence, trailing sessions as the **same-timescale** Bollinger-width percentile basis; VIX; a
staleness-gated `cache:options:matrix` row for dealer-gamma; prior-threaded; degrades to an
`unclear` shell that PRESERVES the smoothing carry) + a **5-min RTH slot** (`scheduler.regime_due`,
polled on the existing 120 s tick → fires within ≤2 min; off-hours the last read persists) +
`handlers._maybe_recompute_regime`/`_publish_regime`/`_record_regime` + a **≤2-min crisis fast path**
(`run_crisis_check`, every refresh, crisis-only evidence, compare-and-set under the lock, writes only
on an attack). Publishes **`cache:sentiment:regime`** (additive **`RegimeState`** contract; carry keys
stripped) + **`cache:sentiment:regime_history`**, and records one row per sample into a new
**`regime_intraday`** table (30-session window) so tuning/validation data accrues from day one.
**Surfaced**: a **Market Regime** panel on `/sentiment` (headline + confidence + transition line +
evidence chips + a **percent-stacked area chart** of today's membership mix, plain chart + synthetic
contiguous axis per the stockChart-freeze gotcha) and an additive **`market_regime`** entry in the
driver's `market_read` (**named that, NOT `structure`/`regime` — both are already taken in driver_svc
for the spread structure and the gamma-briefing regime**). **CONTEXT ONLY — `guardrails.py` is
untouched** (pinned by a test); no scanner tilt, no sizing change. **Phase 2 is offline validation**
(extend `validate_market_state.py`; publish the honest result) and only THEN Phase 3 consumers.
**Three bugs that only LIVE verification caught** (every unit test used fakes and passed): (1) the SPY
lookback asked for `days=6`, which **Schwab rejects with a 400** (`periodType=day` allows only
[1,2,3,4,5,10]) → bars always None → the classifier would have been **permanently "Unclear"** in
production (now 10, with the allowed set pinned by two regression tests); (2) the Bollinger-width
percentile ranked an intraday width against **daily** windows → pinned at ~0, silently distorting
breakout + mean-reversion; (3) `REGIME_TEXT_CLASSES` was a list where NiceGUI's `.classes(remove=)`
splits a **string** → every `/sentiment` load 500'd. Also: **Schwab serves NO `$VIX1D` price history**
(live-probed: 0 candles, while `$VIX`/`$VIX3M` return them), so the VIX-spike crisis tell runs off an
in-process session latch over the quote the service already fetches (probed once/day, 4-day staleness
bound, degrades to None — never a fabricated spike). **Restart `sentiment_svc` + `driver_svc` + the
webgui.** Green: sentiment-dashboard **408**, sentiment_svc **187**, driver_svc **218**, webgui **877**,
contracts **46**; ruff clean. Live-verified end-to-end (real proxy → label "Trending", confidence 0.50,
evidence "ADX 64 rising / Band-hug 67% / 2 failed OR breaks"; contract-valid with no carry leakage;
RTH gate correctly silent off-hours). Design/plan:
[design](docs/plans/2026-07-23-market-regime-blended-classifier-design.md) /
[plan](docs/plans/2026-07-23-market-regime-blended-classifier-plan.md). Prior — 2026-07-22 (**Dealer
gamma-regime flip alert → Telegram + Discord**: a new options-flow alert (commit `a4ad33f`) fires when
spot crosses a symbol's dealer **gamma flip level** — the regime flips **POSITIVE** (spot ABOVE the flip
→ dealers long gamma, volatility dampened) ↔ **NEGATIVE** (spot BELOW → dealers short gamma, volatility
amplified). Rides the EXISTING 1-min flow-alert poll and pushes via the same `send_flow_alert` path
(Telegram + a **dedicated** Discord webhook), so it is a third flow-alert type beside crossover + UOA.
Pure detectors in `services/options_svc/flow_alerts.py`: **`gamma_regime(spot, flip, prev, band_pct)`**
computes the regime from spot-vs-flip with a **Schmitt-trigger hysteresis band** (spot must clear the
flip by `band_pct` to switch, so a spot hovering AT the flip does not chatter), and
**`detect_gamma_flip(...)` → `(alert|None, new_regime)`** (pure; no alert on the day's baseline / on no
change / on unclassifiable data; `alert_text` describes the mechanic with NO buy/sell claim). New
**`gex_history_db.latest_spot_flip(conn, symbol, view, date)`** reads the latest `(ts, spot, flip)` in ONE
query (summary columns only, never `gex_json`). The handler **`_run_gamma_flip`** (wired into
`run_flow_alerts`, reusing the SAME open read-only connection + cooldown map) records the last-alerted
regime per symbol in a **date-scoped state key `cache:options:gamma_regime_state`** — so the day's FIRST
snapshot sets the baseline WITHOUT an open-time false alert and only genuine intraday crossings fire; a
per-symbol cooldown caps re-fires (an on-cooldown flip keeps prior state so it can fire post-cooldown).
It runs **regardless of the collector lock** (it reads whatever's in `gex_history.db`, written by
whichever collector owns the lock). Push (`push_notify`): `to_positive` → **green**, `to_negative` →
**red**, routed to a new **`discord.flow_gamma_flip_webhook_url`** (falls back to the general webhook).
Config: a **`[gamma_flip]`** block in `config/flow_alerts.toml` — `enabled` / `band_pct` (0.15%) /
`cooldown_min` (60) / **`symbols = ["$SPX","SPY","QQQ","IWM"]`** (empty `[]` = the whole flow universe;
gamma flip is a clean read on heavily-optioned index/ETF names, noise on illiquid ones) — with the
webhook itself in the gitignored `shared/notifications.json` (`.example.json` documents the key). Three
gates as with the other flow alerts (`flow_alerts.toml enabled`, `gamma_flip.enabled`, notifications
`enabled`). **Restart `options_svc`.** TDD per layer; green: flow_alerts + push_notify **100**, handlers
flow/gamma **22**, gex_history_db **41**, options_svc **695 passed / 2** documented `test_expected_move`
baseline; ruff clean. **Live-verified end-to-end**: the running service recorded the day's baseline
regimes ($SPX/SPY positive, QQQ/IWM negative), and a marked test alert **delivered to both channels**
(Telegram HTTP 200, Discord HTTP 204 on the dedicated webhook). Prior — 2026-07-21 (**Market Dashboard: "Magnificent 7" frame → "Top 10", MAG7 tile →
BIG10**: renamed the mega-cap frame category **"Magnificent 7" → "Top 10"** (`symbols.py` `_MAG` +
`CATEGORY_ORDER`) and the composite basket tile **"MAG7" → "BIG10"**; the composite already aggregates
all **10** members (the Mag-7 + AVGO/PLTR/AMD — avg day-move, "N/10 up" breadth, dollar-weighted
net-premium skew). The market ticker was repointed (`webgui/pages/ticker.py` reads the `"Top 10"`
category → emits a `BIG10 …` item). All pure-data + a category-string read; `build_dashboard`/the page
render generically. market_svc **61** + webgui **870** green; live-verified (frame header "TOP 10",
composite "BIG10 −4.00% / 0/10 up / Put 30%", no "Magnificent 7" anywhere, no console errors). **Restart
`market_svc` + the webgui.** Prior — 2026-07-21 (**Market Dashboard: MAG7 frame → 10 names + `$MGTN` index tile**:
(1) added **AVGO, PLTR, AMD** as constituent tiles in the Magnificent 7 frame (each with a call/put
premium subline like the other mega-caps), and **expanded the `MAG7` composite basket from 7 → 10
members** so its avg day-move, breadth ("N/10 up") and net-premium skew now span all 10 (the tile keeps
the `MAG7` label). (2) added an **`$MGTN`** quote tile ("MGTN") to the **Options Sentiment** panel — the
**CBOE Magnificent Ten Index**, an index level colored by day %-move. The user knew it as the
ThinkorSwim symbol **`IMGTN:CGI`**, which the Schwab market-data **API rejects** (`invalidSymbols`) — the
`:CGI` suffix is a ToS index/internals feed the API doesn't serve; the API equivalent
**`$MGTN`** was found via the proxy's `/instruments` **`symbol-regex`** search (`.*MGTN.*` →
"CBOE MAGNIFICENT TEN INDEX", assetType INDEX) and quotes cleanly. All pure-data (`symbols.py`
`_basket` members + `_q` tiles); `build_dashboard`/`_attach_prem`/the page render them unchanged
(generic over basket members + prem tiles + index quotes). SYMBOL_MAP 64 → 68 tiles. market_svc **61**
green; live-verified during RTH (all 10 mega-caps render with premium sublines; MAG7 composite spans 10;
MGTN = 476.85 / −3.1% in Options Sentiment beside Put/Call + Net Prem; uniform 92px tiles; no console
errors). **Restart `market_svc`.** Prior — 2026-07-21 (**Market Dashboard per-symbol premium sublines + equal-height tiles**:
follow-up to the Net Prem tile — the **index (SPX/NDX)**, **broad-ETF (SPY/DIA/QQQ/IWM)** and
**Magnificent-7** tiles now carry a small **per-symbol call/put PREMIUM skew subline** ("Call 37%" /
"Put 11%" / "Even" / "—"), and the **MAG7 composite tile shows the dollar-weighted net of its 7
members**. Data path: `matrix.build_rows` now exposes raw `call_prem`/`put_prem` per row (additive);
`market_svc.compute.read_symbol_premiums(bus)` (version-gated, like `read_net_prem`) reads them into a
`{symbol: (call, put)}` map; the tiles are flagged `prem=True` in `symbols.py`; `build_dashboard._attach_prem`
sets `prem_skew_pct` per tile (a quote tile looks itself up by `quote_symbol`; the MAG7 basket
Σcall/Σput-aggregates its members via the pure `symbol_premium_skew`); the page's `prem_line`/`tile_text`
render it as a third label. **All dashboard tiles are now a fixed `min-h-[92px]`** so every tile in a
frame is the **same height** whether or not it has a premium subline (measured: all 64 tiles = 92px).
A name not in the collected universe → "—". TDD (matrix row 1, market_svc compute 6, webgui tile 2);
options_svc matrix **25**, market_svc **60**, webgui **867** green; **live-verified during RTH** (SPX
Call 51% / SPY Call 37% / AMZN Put 11% / MAG7 Call 33% = net of 7, RSP correctly no line, uniform 92px
tiles, no console errors). **Restart `options_svc` + `market_svc` + the webgui.** Prior — 2026-07-21 (**Market Dashboard "Net Prem" tile — dollar-weighted call/put premium
skew**: the **Market Dashboard** (`/market`) OPTIONS SENTIMENT frame gained a second tile beside
**Put/Call** — **Net Prem**, the **money-weighted net call/put premium** across the ~45 already-collected
symbols (index base + `Top 20.xlsx`) — "Call 46%" (call$ dominant, green) / "Put 22%" (red) / "Even" /
"—", with a net-$ subline ("+$2.76B"). **No new data collection**: every 1-min GEX poll already stores
per-symbol cumulative `call_prem`/`put_prem` in `gex_history.db`; a new PURE
`options_svc.matrix.market_premium_aggregate(raw)` sums them dollar-weighted (so index/mega-cap premium
dominates — a market-money read) → `{call_total, put_total, net_m, skew, skew_pct, symbols}` attached to
**`cache:options:matrix` → `premium`** in `compute.build_matrix` (additive; `MatrixSnapshot` gained an
optional `premium` field). `market_svc` reads it via a version-gated `compute.read_net_prem(bus)` (mirrors
`read_sector_pcr`) → a new **`options_net_prem`** external tile in `symbols.py` (Options Sentiment,
polarity `normal` = call-money → risk-on/green; value-only mild coloring, like the sibling Put/Call);
`build_dashboard` branches the external kind on `e["source"]`, carrying the raw aggregate on the tile,
and the page's `tile_text` formats "Call 46%"+subline (Tier-1). **Honest caveat (tile tooltip + help
text):** premium is UNSIGNED cumulative (Schwab has no tape) so it's a **money-weighted Put/Call, NOT net
buying**; dollar-weighting means indices/mega-caps dominate. **First attempt put the tile on the
Sentiment page's Signals matrix; the user redirected it to the Market Dashboard, so the sentiment-side
consumption was reverted** (the options_svc `premium` aggregate stayed — it now feeds the dashboard). TDD
per layer (matrix aggregate 4, market_svc compute 6 + symbols, webgui tile_text 2); options_svc
**723**/2-baseline, market_svc **55**, webgui **865** green; **live-verified during RTH** (Net Prem =
+46% net-call / +$2.76B / 44 symbols, green tile beside Put/Call, no console errors). **Restart
`options_svc` + `market_svc` + the webgui.** Also this session — the **Market Trend & Sentiment section
was split into 4 tabs** (Sentiment · Sector & Industry · Sector Rotation · RRG) and the Sentiment "as of …"
line was dropped (see the "split into 4 tabs" Prior entry below). Branch `Using_Highcharts`. Prior — 2026-07-20 (**Options Matrix Display tab** (`/options/matrix`, a **main-menu (left-rail)
item directly under the Options group** — its OWN standalone page via `main.OPTIONS_RAIL` [rendered by
`_nav_link` right after the Options group entry], NOT an Options tab-strip entry; moved out of
`OPTIONS_CHILDREN` per the user's request so it's a top-level menu item, not a subtab) — an at-a-glance
**sortable grid of EVERY watchlist stock** (the
~45-symbol `gex_collector.collection_symbols()` universe minus `$VIX`), one row per name, to **spot
opportunities** fast. Columns: Ticker · Spot · Day % · **Intraday trend** (▲/▬/▼ from the day's spot slope)
· **Call/Put flow acceleration** (recent-slope-vs-day-average of cumulative call/put premium — "is
call/put-buying heating up") · P/C ratio · Net premium ($M) · **GEX regime** (spot vs stored gamma flip) ·
**# Signals** · **# Flow alerts** (today) · a **Buy/Neutral/Sell** options-flow composite · a **Hotness**
sort key (default sort, hottest float up). **Architecture — a new aggregator in `options_svc`, page is a
pure Tier-1 reader.** Pure derivation `services/options_svc/matrix.py` (`intraday_trend`/`flow_acceleration`/
`composite_signal`/`pc_ratio`/`net_premium_m`/`gex_regime`/`hotness`/`build_rows` — no I/O, all thresholds
named constants, per-symbol-guarded so one null-premium symbol can't zero the grid). `compute.build_matrix`
(DB-only orchestration: per symbol reads the intraday flow series via `gex_history_db.load_flow_series` +
the latest gamma flip via a NEW cheap **`gex_history_db.latest_flip`** [selects ONLY the `flip` column — no
whole-session grid decode, avoiding the documented hotspot], counts signals from `cache:options:scan_day` +
flow-alerts from the UNCAPPED `cache:options:flow_alert_cooldowns` seen-map [each cid `{SYM}|...` is one
distinct daily event — the true per-symbol count, NOT the 50-capped `cache:options:flow_alerts` rolling list
that undercounts once the day fires >50 alerts; caught by the user + fixed 2026-07-21] grouped by `symbol`)
→ publishes **`cache:options:matrix`**
(`MatrixSnapshot` contract) from **`handlers.collect_gex_history`** on the existing **1-min GEX branch** (no
`scheduler.py`/`app.py` edit), plus a **~30 s live spot/day% overlay** (`compute.apply_live_spots` +
`handlers.refresh_matrix_spots`, one batched `get_quotes`) on the `refresh_header` tick so Spot/Day% feel
live. The webgui **`webgui/pages/options/matrix.py`** page is engine-free — version-polls `cache:options:matrix`
every ~2 s and repaints a sortable `ui.table` in place (Tailwind-first, colored Buy/Sell + trend + regime
cells; default order = server-sorted hotness-desc). **Counts + flow gate on `session_date`, not `today`**, so
off-hours the grid shows the last session's flow with ITS own counts. **A date-type bug was caught by LIVE
verification and fixed**: the count gate compared the string `scan_day["date"]` against the **`datetime.date`**
returned by `active_session_date()` → all-zero counts on every row; fixed by normalizing `session_date` to an
isoformat string for the gate + payload field while keeping the date object for the DB reads (a regression
test now passes a real `datetime.date`). **Live-verified end-to-end** against the running stack (45 rows,
counts populated — $NDX 135 sig → hotness 275, AMD 7 flow alerts — sorted hotness-desc, colored badges, the
live-spot overlay updating spots off-window; no console errors). **Restart `options_svc` + the webgui.** TDD
per layer (implementer + spec + code-quality review each): contracts 42, matrix pure **21**, options_svc
**677**/2 baseline, gex_history_db **40**, webgui **861**; ruff clean. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-20-options-matrix-display-design.md) /
[plan](docs/plans/2026-07-20-options-matrix-display-plan.md). Prior — 2026-07-20 (**X/Twitter public-post notification channel + Swing Scanner liquidity
fixes** — two pieces. **(1) Twitter channel.** A fourth notification channel
(`services/options_svc/push_notify.py`) posts new SCANNER (0-DTE + swing) signals to a public
X/Twitter account, riding the SAME `notify_signals` fan-out as Telegram/Discord/Fi-SMS.
`twitter_signal_text` builds a **≤280-char** tweet — compact signal body + a CONFIG-DRIVEN static
footer (hashtags · Discord invite link · extra promo text · disclaimer); budget-defended so the
footer always survives and only the body truncates. `send_twitter` is a **tweepy v2 / OAuth 1.0a**
sender (lazy import; best-effort — a duplicate-content **187**, rate-limit **429**, or network error
is caught, never raised into the scan/publish path). `notify_twitter` is a **scanner-only** fan-out
with its OWN gates, independent of the private channels: a PUBLIC `min_score` (only your stronger
signals go public while weaker ones still push privately) + a **persisted per-day `daily_cap`**
(quota guard vs. the free-tier monthly write cap + spam-flagging), `dry_run` = format+log without
posting. Wired into `notify_signals` (kind=="scanner" only), guarded so a Twitter failure can NEVER
break the Telegram/Discord/SMS sends. **The signal GRADE** (Strong/Good/Marginal/Weak from the flat
scanner's composite ≥80/≥60/≥40/else) is now shown on the tweet AND on `telegram_signal_text` +
`discord_signal_embed` (new Grade field) — all three tolerant of a missing grade. Config: a `twitter`
block in `shared/notifications.json` (+ `.example.json`) — `enabled`/`dry_run`/`min_score`/`daily_cap`
+ 4 OAuth keys + `hashtags`/`discord_url`/`extra_text`/`disclaimer` — with `TWITTER_*` env overrides.
**Ships OFF** (`enabled:false` + `dry_run:true`) → inert until real keys are added AND both flags
flipped; **nothing publishes by default** (X account creation + the go-live flip are the user's — a
public-publish action). New dep **`tweepy>=4.14`** (installed 4.17.0, pinned). TDD; push_notify **73**
+ shared/notify **14** green, options_svc 629/2 (the documented pre-existing `test_expected_move`
baseline), ruff clean; verified end-to-end in dry-run (strong signals formatted w/ grade+footer, weak
one gated out of the public feed, nothing posted). **(2) Swing Scanner liquidity fixes**
(`options-scanner/strategy_scoring.py`) — the Swing Scanner (`/options/swing`) graded EVERY candidate
on non-index symbols (AAPL/MSFT/IWM) **Weak** with "Fails: liquidity" + `q_liq=0.0`. Root cause:
`q_liq` delegated to the FLAT scanner's `scoring.norm_liquidity` — a percent-of-mark band (hard 0 at
≥5%) calibrated on index options (`$SPX`/SPY/QQQ, penny-wide on high marks) and DESIGNED as a soft
5/100 ranking factor, but the Swing Scanner promoted it to a HARD gate (a 0 caps composite at 39 →
Weak). Fixed with a LOCAL, tick-aware `norm_liquidity_ticks` scoring the spread on percent-of-mark
**or** quoting-ticks, whichever is more forgiving (mirrors the flat scanner's own
`passes_liquidity_gate` hybrid). A SECOND fix: `q_liq` averaged a neutral-50 placeholder for unquoted
legs into the real measurement, compressing every credit structure into [25,75] and making Strong
unreachable for the CREDIT/NEUTRAL families — now unquoted legs are SKIPPED. **`scoring.norm_liquidity`
is deliberately UNTOUCHED** (it feeds the flat scanner's `calc_composite_score`, which the driver
sizes paper trades from — keeping the recalibration local confines the blast radius). Live-verified
(IWM/AAPL/MSFT all-Weak → Good/Marginal; SPY/QQQ unchanged; genuinely-wide ZM still Weak). Green:
strategy_scoring/scanner/scoring **125**, options_svc **629/2** baseline, ruff clean. Commits
`be94c7a` (tick-aware) / `80617aa` (leg-dilution) / `800adf7` (Twitter). Branch `Using_Highcharts`.
Prior — 2026-07-18 (**Flow alerts → contract-level Unusual Options Activity (strike/cost/
expiry/premium)** — a follow-up to the 2026-07-17 flow alerts: the **"unusual activity"** alert is now a
per-contract **vol/OI** detector that NAMES the specific option and carries the fields the user asked for.
The aggregate per-minute volume-spike (`detect_spike`, rolling baseline) is **RETIRED**; the new pure
**`flow_alerts.detect_uoa(symbol, chain, cfg)`** walks the live chain (all strikes/expiries, calls+puts),
qualifies a contract when **volume/openInterest ≥ K** (default 3×) **AND** volume ≥ `vol_floor` (500)
**AND** premium (`mark·vol·100`) ≥ `premium_floor` ($250k — real money only), **skips `oi ≤ 0`**, and
returns the **top-N by premium per symbol** (default 3; 0-DTE stays in — the premium floor + cap tame it).
Each alert reads e.g. **`SPY 07/18 450C — UNUSUAL: 8,200 vol vs 1,300 OI (6.3×) · $1.85 · $1.52M
premium`** (Strike + C/P · Expiry [MM/DD, `0DTE` tag] · Cost [mark] · Premium [$, humanized] · vol/OI).
**No re-fetch:** UOA is computed inside the 1-min GEX poll's existing **`on_chain`** hook
(`compute.collect_gex_snapshots` → `stash_uoa` → consume-once `_UOA_STASH`), so it reuses the chain the
poll already fetched; `handlers.run_flow_alerts` drains the stash and emits contract alerts **once per
contract per day** (the cid `{sym}|uoa|{side}|{strike}|{expiry}` doubles as a date-scoped seen-set — vol/OI
is monotonic, so a contract crosses K once and pings once). UOA shares the **crossover's `$VIX`-excluded
universe** and is gated by the same `enabled` kill-switch (skipped in `on_chain` when off). The
**crossover** alert is unchanged but now shows the explicit premiums (`$SPX — call premium overtook puts:
$2.10M calls vs $1.95M puts (bullish flip)`). Delivery (Discord/Telegram push + webgui toast+chime) is
unchanged — the richer `text` flows through both; `push_notify._flow_is_bullish` + the webgui `_tick`
bullish check were repointed `spike`→`uoa` so a UOA **call** renders GREEN. **`config/flow_alerts.toml`**
`[spike]`→`[uoa]` (`k`/`vol_floor`/`premium_floor`/`top_n`). **The Windows desktop-notification "not
working" report was diagnosed as NOT a code bug** — the flow branch calls the same `notify_desktop` as the
working scanner alerts; it needs `desktop_notifications` ON + browser permission granted + Windows allowing
browser notifications + an alert actually firing (08:00–15:20 CT). **Restart `options_svc`.** TDD per layer
(spec + quality review each); options_svc flow suites green. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-18-flow-uoa-contract-detail-design.md) /
[plan](docs/plans/2026-07-18-flow-uoa-contract-detail-plan.md). Prior — 2026-07-19 (**efficiency re-audit → Medium + Low tier remediated**:
the follow-up batch to the 2026-07-18 Critical/High fixes — TDD per item, all suites green
(webgui **854**, options_svc **596** [2 pre-existing date-relative `test_expected_move`],
sentiment_svc **144**, driver_svc **211**, market_svc **49**, proxy **98**, bus **24**).
Flow alerts read only the trailing ~22 rows + normalize once + drop `$VIX` + mtime-cache the
TOML + `skip_unchanged` the cooldown; `gex_json` grids **zlib-compressed at insert** (~5×) +
redundant `idx_snap_today` dropped + `init_schema` once/process; **term chain polls 5-min**;
rescue advisories use a **light GEX-only context** (no full `gamma_snapshot`);
`reprice_captured` clears the chain cache first (freshness); proxy stats counter **WAL** +
`_rate_limit` locked + reconcile log demoted; market_svc summary → **background task** +
weekend throttle + version-gated pcr; sentiment push **outside `_TREND_LOCK`** +
`sector_pc_delta` conn closed; driver reads the composite **once/cycle**; webgui ticker/market/
driver reads moved **off the event loop** + driver version-probes pipelined + scanner rows built
off-loop + page-build `options:scan` read once. Gamma `_render_view` left on-loop (crop already
tamed it — poor risk/reward). See "Performance characteristics & known hotspots". Branch
`Using_Highcharts`. Prior — 2026-07-18 (**efficiency re-audit → all Critical + High fixes**: a
four-agent audit of the grown app found — and this session fixed, TDD per layer —
(1) **~37% of 1-min GEX slots silently dropped** (serial 24-chain fetch + the
scheduler gathering all branches before sleeping) → `poll_once` now fetches chains
in a pool + `scheduler.launch_branches` fires keyed non-blocking background tasks
with a still-running skip (also un-distorts the flow-alert spike baselines);
(2) `gamma_snapshot`'s **whole-session grid re-decode every minute** → incremental
per-(symbol,view,date) memo + `load_date_with_grid(since_ts=…)`; (3) the same
tick's **double chain fetch** for the viewed symbol → `poll_once(on_chain=…)` +
a consume-once tick-chain stash; (4) webgui watcher regressions — `read_metas`
(pipelined `:ver`+`:ts` probes; `cache_set` now writes a `{key}:ts` side key) +
the TTL-bypassing health re-warm; (5) sentiment's **~4,400 Schwab calls/day** —
sector P/C TTL-cached 15 min + `compute_30d_trend` self-fetch cached hourly. See
"Performance characteristics & known hotspots" for the full record + the still-open
Medium items. Branch `Using_Highcharts`. Prior — 2026-07-17 (**Options-flow alerts — put/call premium crossover + unusual activity**:
new **in-app popup (toast + chime) + Discord/Telegram** alerts on two events, detected server-side in
`options_svc` riding the **1-min GEX poll** over the **whole collected universe** (~24 symbols). **(1)
Crossover** — a symbol's daily-cumulative call **premium ($)** crosses its put premium (money-weighted
sentiment flip; `detect_crossover` fires on a net-sign flip that clears a hysteresis **band** [2% of the
larger side] AND a **`min_premium`** floor [$10k] so tiny open-session premiums don't chatter). **(2)
Unusual activity** — a per-minute **volume** increment (this snapshot − last) spikes to **≥ K× the
symbol's own trailing average** (`detect_spike`, K=4 over a 20-min window) AND clears an absolute
**`floor`** (500 contracts); the relative test ALWAYS applies via `k × max(baseline, min_baseline)` so a
dead-quiet name can't fire on the floor alone, plus a **warm-up** (`min_points`) for the first minutes.
Data is **unsigned/cumulative** (Schwab has no tape) so alerts say "unusual activity", never "buying".
**Architecture** (mirrors the existing signal/action pushes): pure detectors in
`services/options_svc/flow_alerts.py`; a `handlers.run_flow_alerts(bus)` (wired into `collect_gex_history`
after `publish_flow_skew`, best-effort/guarded — a flow-alert failure can NEVER break GEX collection)
iterates the universe on **one reused read-only `gex_history_db` connection**, reads each symbol's day
flow series (`load_flow_series`), detects with a **date-scoped Redis cooldown map**
(`cache:options:flow_alert_cooldowns`, keys `{sym}|crossover` / `{sym}|spike|{side}`, 30/20-min cooldowns
so a fired signal pings ONCE), pushes each fresh alert via `push_notify.send_flow_alert` (Telegram HTML +
Discord embed, **green = bullish** [calls overtook / call surge] / **red = bearish**), and appends
(deduped by `id`, capped 50, date-scoped) to **`cache:options:flow_alerts`**. The webgui's existing 2-s
watcher (`main.py` `_watcher_compute`/`_tick`) reads that key, diffs new alert `id`s vs
`_ALERT_STATE["flow_acked"]` (seeded on the first tick so a page load doesn't replay the day's backlog),
and fires `play_alert` + a colored `ui.notify` toast (+ optional desktop notification) — reusing the
alert-sound/volume/desktop settings, gated by a new **Settings → "Flow alerts"** toggle
(`app_settings.flow_alerts_enabled`). **Three independent gates:** `flow_alerts.toml enabled`
(whole-feature server kill-switch → nothing published → webgui silent), the notifications-config `enabled`
(phone push), and the webgui toggle (popup only). **Thresholds live in `config/flow_alerts.toml`** (K /
band / floor / min_baseline / min_premium / window / cooldowns — edit + restart to tune). Runs only in the
08:00–15:20 CT poll window (automatic). **Restart `options_svc` + the webgui.** Built
subagent-by-subagent, TDD per layer (spec + quality review each); options_svc **579** (+2 pre-existing
date-relative `test_expected_move` fails) + webgui **844** green. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-17-flow-crossover-unusual-activity-alerts-design.md) /
[plan](docs/plans/2026-07-17-flow-crossover-unusual-activity-alerts-plan.md). Prior — 2026-07-16 (**Scanner: directional trades + day-persistent signals**: the Options Scanner (`/`)
gained a third sub-tab — **Directional** — and its tables now hold **the whole day's signals**, not just the last
scan's. **(1) Directional.** `scanner_engine.run_full_scan` emits a NEW **`signals_directional`** list (single-leg
`LONG_CALL`/`LONG_PUT`/`SHORT_CALL`/`SHORT_PUT`), built per symbol per DTE window by **reusing**
`strategy_scanner.build_directional` + `strategy_scoring` (already proven on `/options/swing`) against chains the
scan already fetched. **Own tab + own scorer, deliberately:** options-scanner's `scoring.py` is a premium-seller's
model that *structurally cannot score a long call* (rewards positive theta, penalizes long vega, needs a short
strike), so directional is scored on **Fit+Quality** and its score **never sits beside a premium composite**.
`em_1sd` is computed **per window** (a single 1-day EM would under-score the swing side by ~17.5 composite points).
Naked shorts render **`Max L = ∞`** + an **undefined-risk** badge and **cannot be paper-traded**. The list is
invisible to the **autonomous driver by construction** (`build_packet` merges only `signals_0dte + signals_swing`;
the `{PCS,CCS,IC}` allowlist is the second layer) — **pinned by a synthetic probe** (an allowlist-PASSING PCS parked
in `signals_directional`), because the two defenses are REDUNDANT and a realistic test can't tell them apart.
**(2) Day persistence.** `rescan` now publishes a **SECOND key `cache:options:scan_day`** — a date-scoped union
`{date, signals_*, truncated?}` (pure `compute.merge_day_signals`, id-keyed): a still-qualifying signal takes the
**fresh** numbers, a dropped-out one is **carried forward frozen** (`live=False` + `stale_since`) and renders
**dimmed + "Dropped HH:MM"**. **`cache:options:scan` keeps live-only semantics verbatim** — the driver reads it and
must never be offered a signal that no longer qualifies. Date is **CT-pinned via `_today_ct()`** — NOT
`active_session_date()`, which flips at 08:30 while scans start at 08:00 and would wipe each morning's first two
scans. **Capped at 2000/list** (evict oldest-stale-first, **never evict a live signal**, log + a `truncated` block
the page surfaces): measured worst case was **~17 MB**, at the 16 MB `cache:options:gamma` payload that forced the
P2 crop. **The page gates its render on `payload["date"] == today_ct`** — the merge is best-effort and fails
**stale, not absent**, so yesterday's envelope still carries `live=True` rows. **(3) "New" reworked + a bug fixed.**
Now means **unseen since you last viewed the page** (acknowledged **only on `render()`'s initial paint**, never on a
version-poll repaint — otherwise a repaint while you're away acknowledges signals you never saw). **The old marker
was broken:** `_sig_key` rebuilt a key from `short_strike`/`long_strike` but was fed DISPLAY rows where
`signal_rows` merges both into one `strikes` cell, so every key collapsed to `SPY|PCS|None|None|07/17` and a new
signal at different strikes went unmarked. **Now keyed on the engine's unique `id`.** **(4) A live bug fixed on the
way in** (`/options/swing` had it too): `payoff_metrics` set `unbounded=True` for **both** an unbounded PROFIT (long
call) and an unbounded LOSS (naked short), so a short call rendered **`Max P = ∞`** while its genuinely unlimited
loss showed as a finite margin proxy — exactly inverted. Now emits `unbounded_profit`/`unbounded_loss`.
**Persistence created one new hazard, closed:** a dropped signal is frozen at an hours-old price and `paper_create`
records `signal['credit']` **verbatim, no re-pricing** — so the **Paper button is gated off on stale rows** (all
three tabs; Calculator/Expected-Move stay open — reviewing a dropped signal is the point, booking it is the
hazard). Table reads moved **off the event loop** (`run.io_bound`) + `rowsPerPage: 100` (was unbounded → up to
~6,000 DOM rows). **⚠ KNOWN — the Directional tab's ranking is dominated by a pre-existing scoring artifact
(`strategy_scoring`, affects `/options/swing` equally):** a long put's max profit is *bounded* at S=0 (underlying →
$0) so it always gets a finite R:R (measured up to **1404:1**), while a long call's is honestly **unbounded** →
`rr=None` → a PoP proxy. Live result: **LONG_PUT avg score 59.2 / best rank #1; LONG_CALL avg 45.2 / best rank
#14 — all top 12 were LONG_PUT**, i.e. *being honest about unlimited upside is penalized ~14 points*. The #1 signal
was an ATM IWM 295 put **19 minutes from expiry** graded "Good — passes all quality gates" on an **884:1** R:R that
only pays if IWM hits $0 today. **Not fixed** — it's a scoring-model decision, not a bug, and it changes Swing too.
**Restart `options_svc` + the webgui.** options-scanner **1269** / options_svc **564** (2 pre-existing
date-relative `test_expected_move` fails) / driver_svc **209** / contracts **40** / webgui **840** green; ruff
clean. **Live-verified end-to-end during RTH**: all 4 types built, day key accumulated across two scans with **21
0-DTE signals frozen at 14:42** as they stopped qualifying, driver menu provably free of directional. **Test
baselines in `options-scanner/CLAUDE.md` were badly stale (667/2 vs a real 1260/15) and were corrected.** Branch
`Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-16-scanner-directional-day-persistence-design.md) /
[plan](docs/plans/2026-07-16-scanner-directional-day-persistence-plan.md). Prior — 2026-07-15 (**Nav drawer → icon rail that expands on hover**: the webgui's left drawer
(`webgui/main.py`) is now a **64px icon rail** that widens to **248px on hover** and **OVERLAYS** the page
instead of reflowing it. **Mechanism** (the part worth keeping): the drawer is LAID OUT at
`NAV_WIDTH_RAIL=64` via Quasar's `width` prop (`drawer_width(pinned)` → 64, or `NAV_WIDTH_OPEN=248` when
pinned); `_NAV_CSS` widens it on hover with `.q-drawer:has(> .nav-drawer:not(.nav-pinned)):hover, :focus-within
{ width: 248px !important }`. Because Quasar's LAYOUT still uses 64, `.q-page-container`'s padding never
changes → the expanded menu overlays content — **deliberate**: this app's Highcharts have no ResizeObserver,
so reflowing on every hover would leave charts mis-sized. No Quasar mini-mode, no JS, no hover round-trips.
The **hamburger pins/unpins** (persisted via `app_settings` `nav_pinned`, default False); pinned lays out at
248 (the page genuinely reflows — correct for an explicit choice) and `.nav-pinned` disables the hover rule.
**Icons render again** (a prior redesign had replaced each item's icon with a colored dot, leaving the `icon`
arg dead): the dot is retired and the icon is the affordance — it's the only thing visible when collapsed.
Two were re-curated — Market Trend & Sentiment `insights`→**`speed`** (the page is four speedometer gauges;
`insights` collided with Trade Analyzer's) and Trade Analyzer `analytics`→**`query_stats`** (the job is
"analyze one symbol", not "charts"); the other five kept (`candlestick_chart`/`dashboard`/`account_balance`/
`smart_toy`/`more_horiz`), guarded by a test asserting the 7 drawer icons stay non-empty + mutually distinct.
**Badges** moved from `ml-auto` onto each icon's top-right corner (Quasar `floating` on a `relative` wrapper)
so a collapsed rail still reports "3 new signals"; `_count_badge`/`_set_badge` DRY the drawer + tab-strip
construction and the 2s watcher's updates. **The two traps this exposed are recorded in the "NiceGUI gotchas"
section — read them before touching drawer CSS or measuring anything in the automation browser**:
(1) `.nav-drawer` is NOT the `<aside>` (the width lives on the parent `.q-drawer`; reach it via
`:has(> .nav-drawer)`), and (2) CSS transitions freeze at their START value in the backgrounded automation
browser, so `getComputedStyle` LIES until you kill transitions. Also: the active-icon accent must be
`.nav-drawer .nav-active .nav-icon` (0,3,0) `!important` to out-specify `theme.build_nav_css`'s
`[menu].text` rule — a `.nav-icon-active` class was tried and REMOVED (at (0,2,0) it tied and lost on source
order). webgui **772** green; ruff clean. Branch `Using_Highcharts`. Prior — 2026-07-14 (**Rescue coverage — Phase 1c: single-type condors & butterflies (ad-hoc)**: the
`/options/rescue` **ad-hoc** tab now builds advisory rescues for the **single-type range structures** —
`CONDOR_CALL`/`CONDOR_PUT` (long condor: long K1 / short K2 / short K3 / long K4) and `BUTTERFLY_CALL`/
`BUTTERFLY_PUT` (long 1-2-1 fly: long K1 / short 2×K2 / long K3). All defined-risk **DEBIT**, neutral/range
(`IC` + `IRON_BUTTERFLY` were already covered — the mapper folds an iron fly into `IC`; this is the all-call OR
all-put sibling family). **Advisory-only** (ad-hoc has no Apply). Now **12 of 19** structures; the rest still pop
"not available yet". Engine (`services/options_svc/rescue.py`, PURE): `assess_range_risk` (heat =
`min(50, loss_frac·60) + min(35, range_frac·35) + 15 if dte≤5 & range_frac>0.8`, where `range_frac =
|underlying − center| / half_width`, center = midpoint of the SHORT strikes, half_width = center → nearest LONG
wing) + `range_candidates` — commission-aware, `apply_kind="advisory"`: **close** (sell the structure → +cv·100·qty
credit, `new_max_loss=0`) + **roll_out** (+30d same strikes → a debit; skipped if any rolled leg is unpriceable).
The structure carries per-unit `legs`; **structure value `cv = +long −short`** (a long condor/fly you own is
POSITIVE, ~ the debit paid — same convention as the debit-vertical path; an early `+short −long` sign slip was
caught in review + pinned by a non-degenerate compute test). Compute (`compute.py`): `_advisory_from_range`/
`_adhoc_range` mirror the debit path — `_RANGE_STRATEGIES` route in `compute_rescue_adhoc`; every leg priced via
`_make_leg_pricer`, cv falls back to the entered debit off-hours (only `close` survives, as with singles/debit).
Page mapper (`webgui/pages/options/rescue.py`): `_range_spec_from_parsed` recognizes an all-one-type LONG range
structure by aggregating signed net qty per strike (4 strikes `[+q,−q,−q,+q]` → condor; 3 strikes `[+q,−2q,+q]` →
butterfly; a split 2× body folds in; short/credit structures fall through), emitting per-unit `legs` + `quantity=q`
+ a NEGATIVE `entry_credit`; the four codes added to `RESCUE_ADHOC_SUPPORTED`. TDD per layer; green: options_svc
**537** (incl. `test_rescue_range` **15** + `test_compute_rescue` range **+6**) + webgui rescue/shell **52**; ruff
clean. Live-verified end-to-end (SPY call condor + put butterfly → advisory-only, correct close economics + heat).
**Follow-up (same day) — rescue close cards show the LOCKED-IN P&L, not a bare $0.** The card `Max loss` field is
`new_max_loss` = the max loss of the position that REMAINS after the action; a full **close** leaves you flat → $0,
which read as "this trade has no loss." Fixed across ALL close paths: the `RescueCandidate` contract gained
`realized_pnl` (the P&L locked in), set on every close/partial builder (`build_close`/`build_partial_close`/the
single/debit/range close candidates) from `mark.unrealized_pnl` (partial = ×closed-fraction); the page renders a
colored **Realized P&L** cell alongside Gross/Comm/Net and **suppresses the trivial "Max loss after: $0" on a full
close** (partial keeps it — the residual is real). The residual field was also relabeled **"Max loss" → "Max loss
after"**. Live-verified (MU bear-put debit close → Realized P&L −$650 shown, $0 suppressed). Branch `Using_Highcharts`. Design:
[design](docs/plans/2026-07-14-rescue-condor-butterfly-design.md). Prior — 2026-07-14 (**Rescue coverage — Phase 1b: debit verticals (ad-hoc)**: the `/options/rescue`
**ad-hoc** tab now builds advisory rescues for **defined-risk DEBIT verticals** — `VERT_CALL_DEBIT` (bull call =
long lower call + short higher call) and `VERT_PUT_DEBIT` (bear put = long higher put + short lower put) — the
next family after singles (credit spreads · IC/fly · singles · debit verticals = **8 of 19** structures; the rest
still pop "not available yet"). **Advisory-only** (ad-hoc has no Apply). Engine (`services/options_svc/rescue.py`,
PURE): `assess_debit_risk` (directional — "at-risk" = underlying moved against the LONG leg; heat =
`min(50, loss_frac·60) + min(25, otm_depth·300) + 15 if dte≤5 & OTM`, otm_depth from `long_strike`) +
`debit_candidates` — all commission-aware, `apply_kind="advisory"`: **close** (sell to close → +cv·100·qty credit,
`new_max_loss=0`), **roll_out** (sell current + buy +30d same-strikes → debit since the later spread is richer),
**convert_to_butterfly** (SELL short-strike + BUY beyond it → the L/S/(S±w) butterfly, credit that reduces the net
debit). Compute routing (`compute.py`): `_advisory_from_debit`/`_adhoc_debit` mirror the singles path —
`_DEBIT_STRATEGIES` route in `compute_rescue_adhoc`; the two legs are priced directly via `_make_leg_pricer`
(cv = long mid − short mid, falling back to the entered debit when a leg is unpriceable — the off-hours case, where
only `close` survives), underlying from the gamma-snapshot spot, `unrealized_pnl = (cv − |entry_credit|)·100·qty`.
Page mapper (`webgui/pages/options/rescue.py`): `adhoc_spec_from_legs` recognizes a 2-leg debit vertical (BEFORE the
generic net-credit guard, which applies only to credit structures) → `long_strike`/`short_strike` + a **negative**
`entry_credit`; both codes added to `RESCUE_ADHOC_SUPPORTED`. TDD per layer; green: options_svc **516** (incl.
`test_rescue_debit` **11** + `test_compute_rescue` debit **+5**) + webgui rescue/shell **47**; ruff clean.
Live-verified end-to-end (SPY bull-call + bear-put → advisory-only, correct close economics + directional heat).
Branch `Using_Highcharts`. Design:
[design](docs/plans/2026-06-24-rescue-debit-verticals-design.md). Prior — 2026-07-13 (**Paper-trade non-credit structures (long options + debit verticals)**: the
multi-strategy Swing Scanner's **Send to Paper** button now works for **defined-risk DEBIT** structures —
**LONG_CALL / LONG_PUT / BULL_CALL / BEAR_PUT** — not just credit spreads (naked shorts stay excluded:
undefined risk). The ledger (`trades.db`, `paper_trader`) grows a **legs-based DEBIT trade**: `create_paper_trade`
routes those `type`s to `_create_debit_trade`, storing the normalized `legs` + `entry_debit` (the scanner's
per-contract `net_debit`) + `max_loss` (= debit) + `direction="DEBIT"` (a DEBIT reads as a NEGATIVE per-share
`entry_credit` so the existing Paper Trades columns render unchanged). Repricing + settlement are GENERIC over
the legs: new `signal_repricer.reprice_legs` (values each leg at its current mid, long +/short −, → per-contract
unrealized = `value×100 − entry_debit`) + `legs_intrinsic_value`/`position_intrinsic` for expiration
(`paper_trader._expire_debit_trade`); `compute._reprice_open_pnl` routes `direction=="DEBIT"` → `reprice_legs`,
credit spreads keep the tested short/long-strike `reprice_swing` path (zero regression). The Paper Trades page
renders debit legs as `L 450C` / `L 100C / S 105C` (`paper._legs_text`), and `strategy_table._PAPER_TYPES` +
`_allow_paper` now gate the button open for the four debit types. **Units validated end-to-end against the REAL
`strategy_scanner.payoff_metrics`** (net_debit/max_loss are per-CONTRACT ×100; leg `mark` per-share): a $2.50
LONG_CALL ×3 → entry_debit $250, max_loss $753.90 (incl. commission), ITM@112 → realized ($1,200−$250)×3=$2,850.
TDD per layer; green: options-scanner signal_repricer **24** + paper_trader debit **6** + options_svc **466** +
webgui **739**; ruff clean. See [[paper-debit-trade-representation]]. **Restart `options_svc`** to pick it up.
Branch `Using_Highcharts`. Prior — 2026-07-13 (**Manual-book analytics parity + swing-model staleness warning + process
watchdog + gex VACUUM tool**: the follow-on "remaining items" batch. **(1) Manual-book analytics parity.**
The `perf_analytics` engine + `perf_charts` chart builders (equity curve + MAE/MFE) are now shared and
wired to the MANUAL paper account too (`compute.manual_analytics` → `_book_analytics(None)` →
`cache:options:paper_analytics`, published by `refresh_paper_account`; surfaced as an **Analytics** section
on `/options/portfolio`). Because the manual book auto-trades EVERY captured signal while the driver book
trades Claude's SELECTION, the two equity curves are the **scanner-baseline-vs-decider benchmark** — the
answer to "does Claude's selection add edge?" (live: manual −$430 realized vs driver −$2,648, caveat:
different risk caps). The driver page's `equity_curve_figure`/`excursion_text` were extracted to the shared
`pages/options/perf_charts.py` (re-exported in `driver.py`, so both pages render identically). **(2)
Swing-model staleness warning.** `/trade` now shows an amber "⚠ Model is N days old — re-run
fit_swing_model.py" nudge when the `swing_model.json` fit date is >60 days old (`trade.model_staleness`),
so the validated factor model's regime-dependent edge doesn't silently decay (the refit itself stays a
manual offline run). **(3) Process watchdog (opt-in) — `tools/watchdog.py`.** Probes every tier (Memurai
PING / proxy + 6 services `/health` / webgui TCP) and restarts a DEAD process via the same windowless
`tools/restart_one.bat` the Status page uses, **storm-capped** (≤3 restarts / 10 min → then left down +
logged). NOT started by `start_all` — run it yourself for an unattended stack (`python tools/watchdog.py`,
`--dry-run`/`--once`). Closes the deferred R4a gap (a dead PROCESS, vs the in-process scheduler restart).
Live-verified (dry-run sweep: all 9 components healthy). **(4) gex_history.db VACUUM — `tools/vacuum_gex.py`** (since 2026-07-13 also a confirm-gated **Settings → Maintenance → Vacuum GEX history DB** button — runs the tool as an off-thread subprocess and prints before→after; first run reclaimed 1.72→1.46 GB)**.**
Offline maintenance that runs `PRAGMA auto_vacuum=INCREMENTAL; VACUUM;` to SHRINK the DB on disk (the daily
`purge_keep_sessions` frees pages but doesn't shrink the file). **Refuses to run during market hours / while
the collector lock is fresh** (VACUUM locks the DB for minutes) unless `--force`; `--purge` runs retention
first; reports before→after size. (DB currently 1.72 GB.) TDD; green: webgui **738** + options_svc (manual
analytics/handlers) + tools/watchdog **6**; ruff clean. **Restart `options_svc`** for the manual analytics
view. Also — **the five-state order-flow streamers were LIVE-RTH-VERIFIED** this session (below). Branch
`Using_Highcharts`. Prior — 2026-07-13 (**Driver performance analytics + MAE/MFE + proxy hardening + CI/ruff +
order-flow live-verified**: a batch of "know-thyself" analytics, security hardening, and hygiene. **(1)
Driver performance analytics (`/driver` → new "Analytics" section).** A new PURE
`services/options_svc/perf_analytics.py` builds three views over the driver book's positions —
**equity_curve** (daily realized P&L + cumulative-realized equity, bucketed by exit date), **posture
post-mortem** (`posture_stance` groups CLOSED positions by whether they were opened WITH vs AGAINST the
directional posture recorded at entry → win-rate/avg-P&L per stance + a with-vs-against edge, answering
*does the decider win more trading with the tape?*), and **excursion_stats** (MAE/MFE aggregates + MFE-
capture). `compute.driver_analytics()` reads the driver DB → published to a new
`cache:options:driver_paper_analytics` view by `refresh_driver_paper` (every 5-min manage tick). The page
renders a Highcharts equity curve (equity line + daily-P&L columns, built once + updated in place) + a
posture-stance table + an MAE/MFE line (`driver.equity_curve_figure`/`postmortem_rows`/
`postmortem_headline`/`excursion_text`). Live-verified: the equity curve populates from the real book (9
daily points, −$2,648 cumulative realized). **(2) MAE/MFE tracking (#2).** `paper_account_db` gained
nullable `mae`/`mfe`/`entry_context` columns (idempotent ALTER migration); `paper_engine.run_manage_cycle`
rolls each open position's max-adverse/max-favorable excursion on every reprice (`excursion_update`, PURE).
Forward-only (existing positions have NULL until repriced). **(3) Entry-context stamping (#7).** The driver
handler stamps each opened position's `entry_context` (posture + market_read summary + shadow would_block)
— threaded `driver_paper_create` args → `open_driver_position(context=)` → stored as JSON — so the
post-mortem can attribute the ENTRY regime to the realized outcome (forward-only; pre-existing positions
have no context → not attributed). **(4) Proxy hardening (#8) — all backward-compatible/opt-in
(`docs/SECURITY.md`).** `schwab_proxy` CORS now defaults to a **local webgui/proxy allowlist** instead of
`*` (closes the browser-reachable-proxy hole; override via `PROXY_CORS_ORIGINS`); an **optional shared
secret** guards the trading endpoints (`/accounts`/`/orders`/`/positions`/`/transactions` require
`X-Proxy-Secret` — enforced ONLY when `PROXY_SHARED_SECRET`/`shared/proxy_secret.txt` is set, timing-safe;
`proxy_client` auto-attaches it); the **Bus** supports an optional `MEMURAI_PASSWORD` (unset → no AUTH,
unchanged). Defaults preserve today's behavior exactly. **(5) CI + ruff + lockfile (#10).** A GitHub
Actions per-folder test matrix + `ruff check` (lenient `pyproject.toml` config, passes clean) + pinned
security-sensitive deps + `requirements.lock` + `docs/CI.md` (some of this pre-existed from the 2026-07-02
pass and was consolidated/fixed to green). **(6) Five-state order-flow streamers — LIVE-RTH-VERIFIED.**
Probed the live caches during market hours (12:09 CT): `cache:sentiment:order_flow` populates with fresh
Lee-Ready-classified equity CVD + option pressure, and the aggression axis consumes both (visible as
`order-flow`/`option-flow` in `derived.trend.evidence`) — Phases 4-5's pending live check now passes. See
[[streamer-order-flow-deferred]]. TDD per layer; green: driver_svc **203** + options_svc **463** + webgui
**732** + schwab-proxy **91** + shared/bus **20** + options-scanner paper **75**; ruff clean. **Restart
`options_svc` + `driver_svc`** (the analytics view + entry-context stamping go live; the /driver Analytics
section fills once options_svc republishes). Restart the proxy only if you set a secret / CORS override.
Branch `Using_Highcharts`. Prior — 2026-07-13 (**Directional-gate shadow mode + automated EOD close-out push**: two
independent enhancements. **(1) Directional-gate shadow mode (driver_svc).** The wrong-side directional
gate (`guardrails._side_blocked` / `WRONG_SIDE_REGIME`) is still shipped INERT
(`settings.DIRECTIONAL_GATE_ENABLED=False`) because its offline backtest only covered 7/22 trades — so
instead of waiting, `compute.run_cycle` now runs it in **log-only shadow mode**: it computes the decisive
price-truth posture (`_directional_posture`) EVERY cycle and, via the new PURE
`guardrails.shadow_gate(executable, posture)`, records which trades that FIRED a live gate WOULD have
blocked — **without blocking anything** while the flag is off (byte-identical execution to before). The
finding rides an additive `shadow_gate` block (`{posture, would_block:[{id,symbol,structure}], n,
enabled}`) on the run_cycle return → onto each `cache:driver:autonomous` decision-log row (loose dict, no
contract change) → surfaced on `/driver` as an amber "👁 Gate shadow: would block N …" line
(`driver.shadow_gate_line`, shown only while the gate is inert AND it would have blocked ≥1 fired trade).
Every live trading day now accrues real would-have-blocked evidence, so `DIRECTIONAL_GATE_ENABLED` can be
flipped on data instead of the thin replay. When the flag is ON the wrong-side trades are already in
`rejected`, so the shadow is naturally empty. **(2) Automated EOD close-out push (options_svc).** A new
once-daily scheduled slot at **~15:10 CT** (`scheduler.eod_summary_due`, mirrors `action_alert_due`,
trading-day/holiday-gated, 30-min grace) → `handlers.run_eod_summary` → `compute.collect_eod_summary`
(PURE `_eod_book_summary` per book) assembles the day's result for BOTH engine paper books — the MANUAL
account + the isolated DRIVER account — (day P&L = `session_pnl`, equity, open count, halt flag, today's
closed W-L + realized from `exit_ts`-dated closed positions) and pushes a compact digest via the existing
`shared/notify` channels (`push_notify.send_eod_summary` — Telegram/Discord/SMS; sends whenever ≥1 book is
seeded, no empty-content skip since the day's P&L IS the point). Cached at `cache:options:eod_summary` for
inspection. Book state is read AS-IS at 15:10 (no manage cycle is forced first), so a 0-DTE that expired
but hasn't settled still contributes its unrealized. Closes the daily-accountability loop — the day's
RESULT now pushes to the phone alongside the already-24/7 signal + action-alert pushes, no browser needed.
TDD per layer: driver_svc **202** + options_svc (+new push_notify/scheduler/compute/handler tests) +
webgui **726** green; live-verified `collect_eod_summary` end-to-end against the real books (manual/driver
day P&L assembled, digest rendered, handler cached). **Restart `driver_svc` + `options_svc`** to pick both
up. Branch `Using_Highcharts`. Prior — 2026-07-12 (**Market Trend & Sentiment split into 4 tabs**: the monolithic
`/sentiment` page was broken up — the **Sector & Industry Performance** table moved to a NEW
`/sentiment/sectors` tab (`pages.sentiment_sectors`, inserted between Sentiment and Sector Rotation)
and the **RRG chart** moved out of `/sentiment/rotation` into a NEW `/sentiment/rrg` tab
(`pages.sentiment_rrg`, last, after Sector Rotation). Final `SENTIMENT_CHILDREN` tab order: Sentiment
· Sector & Industry · Sector Rotation · RRG. The new pages are thin Tier-3 readers that **reuse the
PURE builders** from `pages.sentiment` / `pages.sentiment_rotation` (so the display transforms + their
tests stay single-source); `/sentiment` still reads `cache:sentiment:sectors` only to fill the
Components popup's Rotation/Sector-Value cells. The **"Daily Sentiment & Trend"** intraday graphs are
now **expanded by default**. Per-page hover/help text (`page_help.py`) updated for the new structure.
webgui **723** green; live-verified all 4 tabs (Sentiment: gauges + expanded graphs, no sector table;
Sector & Industry: full table; Sector Rotation: quadrant map, no chart; RRG: 11-series chart). Branch
`Using_Highcharts`. Prior — 2026-07-12 (**Tabbed navigation + page-chrome overhaul + config-driven theming**:
the webgui nav was REDESIGNED — flat drawer main menu + the active group's child pages as a
**folder-style TAB STRIP across the top** with a **subtab slot** beneath it (`main._NAV_GROUPS` /
`_group_children` / `subtab_slot()`; badges float on tabs, drawer group items carry summed badges;
see "webgui structure"). **In-page view pickers moved into the subtab slot**: Gamma
GEX/Charm/DEX/Vanna/Flow/Term, Scanner 0-DTE/Swing (Run scan right-aligned with the table), and
Simulator Replay/What-if/IV-shock (its Controls+Strategy cards also merged side-by-side into one
card). **Menu renames**: Sentiment→Market Trend & Sentiment, Trade→Trade Analyzer, Driver→Claude
Trades. **Page-header cleanup** across Scanner/Paper/Captured/Paper-Portfolio/Swing/Gamma/Simulator/
Expected-Move/Rescue/Market-Dashboard: redundant titles removed (the tab strip names the page),
action buttons right-justified, row counts bottom-right small; Rescue also dropped its wrapper
cards + went dense. **Config-driven styling**: `config/theme.toml` (palette / semantic / 3D buttons
/ gauge / charts / **typography** [px sizes] / **menu**) + a **Settings → Appearance** in-app editor
(section tabs + clickable swatch tiles + Save / Save-&-restart / Reset — comment-preserving
`theme.save_theme_values`). **Fixes shipped alongside**: sentiment intraday graphs frozen-update bug
(Highstock `chart.update()` throws → plain synthetic-index charts; days packed, no dead space) +
test-fixture rows leaking into the live intraday DB (pytest now isolates it — see
tests/conftest + `intraday_history_db.connect`); hourly RTH sector-P/C recompute
(`sentiment_svc scheduler.sectors_due` — premarket starts no longer blank the P/C column); per-view
staleness thresholds (`alerts.stale_after` — no more false "scanner stale" toasts between 15-min
scans); driver scheduler holiday gate (`driver_svc _HOLIDAYS` — no Claude calls on market holidays).
webgui **723** / options_svc **432** / sentiment_svc **140** / driver_svc **162** green. Branch
`Using_Highcharts`. Prior — 2026-07-11 (**Gamma forward projection on the GEX heatmap + 1-min collection +
condensed header**: the `/options/gamma` GEX heatmap now draws a **forward projection band** out to
the 4pm-ET close — future **15-min** columns re-price today's **standing open interest at flat spot**
(the deterministic charm/time-decay morph: walls sharpen, gamma concentrates ATM into the close), each
contract's current GEX contribution scaled by a **BS gamma time-decay ratio** anchored 1.0 at the
collected "now" column so the seam is continuous (`compute.project_gex_grid`, pure, reuses the engine's
exact GEX formula). An **expected-move cone** (`project_em_cone`, √-time fan) is overlaid so the spot
uncertainty is shown honestly rather than baked into the colored grid. **GEX-only, sticky-strike IV,
hidden off-hours** (no session left → collected-only). The projection rides the EXISTING
`cache:options:gamma` GEX view — `gamma_snapshot` attaches a `projection` block (`{times, grid, cone,
spot}`) computed off the live chain and **cropped to the display window** (`_crop_gamma_views`); the page
appends the future columns right of a dashed **"now" divider** with the cone as faint dashed overlays
(`heatmap_figure(projection=…)`, same interpolated image / colorAxis). The strike/heatmap split is now a
**fixed 40/60** (`_STRIKE_HEAT_SPLIT`, one-line flip to 70/30) — the full day + forward band make the
heatmap the star. **GEX collection cadence dropped 2 min → 1 min** (`gex_collector.POLL_INTERVAL_MIN` /
`scheduler._GEX_INTERVAL_MIN` / `gex_status.STALE_AFTER_SEC=120` in lockstep) and the collection
window now **starts 08:00 CT** (was 08:30 — 30 min pre-open; `_GEX_START` / `gex_collector.START_HOUR`
in lockstep). **The Gamma charts now show PRE- and POST-market**: the overnight blank
(`scheduler.gamma_cleared`) was REMOVED, so the display shows the most-recent-available session 24/7 —
the by-strike bars from the live chain, the heatmap from `active_session_date`, which returns the PRIOR
session premarket and flips to today once collection starts. **NOTE (measured):** premarket adds little
DATA — OI is fixed overnight and `$SPX` (an index) doesn't tick pre-open, so pre-open rows are largely a
re-pricing of static OI; the 08:00 premarket Claude briefing is UNAFFECTED (it reads the live chain, never
the collector DB). **Explain + Analyze + the
4×/day scheduled auto-briefings** now carry a reader-first **"into the close"** forward read
(`_projection_brief` = projected flip / call+put walls / EM band at the close → an optional
`close_outlook` field on the Analyze `submit_analysis` schema + infographic card, and an "Into the close"
block on the Explain infographic). The Gamma **header was condensed 4 rows → 2** (a **Briefings**
dropdown replaces the four auto-briefing buttons; one `·`-separated status strip merges collector status
+ last/next scan + refresh countdown + summary via `status_strip_text`). **Restart `options_svc` + the
webgui.** Built subagent-by-subagent, TDD per layer (2-stage spec+quality review); options_svc **431** +
webgui **698** green. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-11-gamma-forward-projection-design.md) /
[plan](docs/plans/2026-07-11-gamma-forward-projection-plan.md). Prior — 2026-07-10 (**Manual Paper Portfolio → hourly entry+manage cadence**: the
MANUAL paper account's auto-run moved from the every-5-min `manage_due` slot to a NEW
**top-of-the-hour** schedule — entry (open new paper trades from current captured signals) +
manage (reprice + auto-close hits) **once at 09:00–14:00 CT** (last run 2pm; **NO 15:00 run** at
the regular-session close). New PURE gate `scheduler.paper_cycle_due(now, ran_slots)` (trading-day
only, once-per-hour within a 20-min grace, mirrors `analyze_slot_due`) + handler
`handlers.run_paper_entry_and_manage` (entry guarded on an existing account in its own try/except
so an entry failure can't skip manage → `run_manage_and_refresh`); the scheduler `loop` gates it on
`paper_cycle_due` (hour latched in `paper_ran` before the blocking cycle). The isolated **DRIVER**
paper account is UNCHANGED — it stays on the 5-min `manage_due` slot (`run_driver_manage_and_refresh`
now runs alone there). **Trade-off:** the manual book's live P&L + target/stop auto-close now update
hourly, not every 5 min. **Restart `options_svc`** to pick it up. options_svc **419** green; TDD per
layer (8 gate + 3 handler tests + updated loop source-inspection tests). Branch `Using_Highcharts`.
See the "Paper auto-manage" box below. Prior — 2026-07-09 (**Intraday options premium/volume flow — new Gamma `Flow`
view**: a per-symbol intraday chart (a new view inserted **before Term**) of the underlying
**price** + daily-cumulative **call/put premium ($M)** with a **net-premium (call−put)** signed
bottom panel. **Phase 1 (backend):** the 2-min GEX poll now also computes
**`flow_skew.index_call_put_premium(chain)`** = `Σ mark × totalVolume × 100` per call/put
(mid-based, **UNSIGNED daily-cumulative** — Schwab has no time-&-sales tape, so no buy/sell split)
for EVERY collected symbol (index base + `Top 20.xlsx`), stored as additive `call_prem`/`put_prem`
REAL columns in `gex_history_db` (idempotent ALTER migration), read via
**`gex_history_db.load_flow_series(conn, symbol, d)`** → `(ts, spot, call_vol, put_vol, call_prem,
put_prem)` per snapshot. **Phase 2 (frontend):** `compute.gamma_snapshot` embeds a **`flow`** series
(reusing the ONE read-only history connection), and the page's PURE `flow_figure`/`flow_summary_text`
render it as a Highcharts stacked-panel chart (price left-axis + call/put premium right-axis + net
panel) under the new **`Flow`** toggle. Premium is **forward-only** (NULL on pre-Phase-1 rows → the
line starts where collection began); no signed buy/sell is possible from stored data. **Restart
`options_svc`** (its `collect_gex_snapshots` runs `init_schema` [adds the columns] then `poll_once`
[populates them]) **+ the webgui**. options-scanner flow_skew/gex_history **+7**, options_svc compute,
webgui **692** green; TDD per layer. Branch `Using_Highcharts`. Prior — 2026-07-09 (**Driver directional gate + cumulative MTD target** — two fixes
motivated by a forensic review ("C") of the driver's REAL closed book: 22 closed trades,
**−$908 realized / 27% win / PF 0.23**, drawdown to $22,768 (−8.9%), a −$1,946 halt day. Root
cause = **wrong-side selection** — 10 of 11 DELTA_STOPs were **call credit spreads run over by a
rising tape** (CCS bucket −$706 @ 21% win); the stops fire at ~0.35 short delta (sensible, median
~1-day hold) so they're fine — **the entry side is the problem**. The app's own sentiment read was
**bearish (3.92) while price melted up**, i.e. its directional opinions were INVERTED, so the fix
keys on **price truth**, not sentiment. **(A) Directional gate** (`guardrails._side_blocked` +
`WRONG_SIDE_REGIME` + a `posture` kwarg on `apply_guardrails`): hard-block a **CCS in an `up` tape /
a PCS in a `down` tape** (IC exempt), where `compute._directional_posture(market_read)` derives
up/down/neutral from **broad-index change_pct + `$ADVN-$DECN` breadth agreement** (NOT sentiment/bias,
NOT the gamma flip — a volatility regime); `_market_read` now carries per-index `change_pct` from the
dashboard. The gate is **code-authoritative, IC-exempt, and degrade-safe** (posture `neutral` when
data is missing → inert), placed BEFORE the capacity check so a block never eats a slot. It ships
**behind `settings.DIRECTIONAL_GATE_ENABLED` (default False = INERT)** and `run_cycle` forces
`neutral` until the flag is flipped. **Backtest first** (`validate_directional_gate.py`, offline):
replaying the 22 real trades vs SPX spot-trend from `gex_history` — at a 24h lookback it blocks the
**two catastrophic $SPX CCS losses (−$561, clear up-trends) → net +$613 / 66% of the CCS bucket**,
but only **7/22 trades are covered** by history and it's **lookback-sensitive** (30h → net −$49 / 0%);
concept validated on the worst day, but **too thin to auto-enable → flag stays OFF** pending more
coverage / the user's call. **(B) Cumulative MTD target** (LIVE now): the flat $500/day banking
target becomes `effective_target = clamp(N×500 − MTD_realized_before_today, TARGET_FLOOR 250,
TARGET_CAP 1000)` (`compute.effective_target` + `mtd_realized_before_today` + `_mtd_trading_days`),
computed in the handler from the driver book's closed-position MTD realized P&L + a holiday-aware
trading-day count, threaded into `build_packet` + `halt_state` (and the published monitor `target`).
Behind pace → ratchet to $1,000; ahead → ease to $250; **the −$1,500 loss halt + per-trade caps are
UNCHANGED** (only the bank/stop threshold moves — bounded, no chasing via oversizing); fails safe to
$500. Built directly, TDD, per-task commits: driver_svc **196** + contracts **38** green. **Restart
`driver_svc`** — the cumulative target is live immediately; the gate is inert until you enable it.
PAPER ONLY. Design/plan:
[design](docs/plans/2026-07-09-driver-directional-gate-cumulative-target-design.md) /
[plan](docs/plans/2026-07-09-driver-directional-gate-cumulative-target-plan.md). Prior — 2026-07-08 (**Driver market-context block — the decider now reads gamma /
breadth / sentiment (context only)**: the autonomous Driver's Claude decider was blind to market
structure — it saw only `vix` + the five-state label string, yet it trades $SPX/SPY/QQQ credit
spreads whose safety is defined by exactly the gamma flip/walls it couldn't see. It now gets an
additive **`market_read`** in its decision packet: per-index **gamma flip / call+put walls / max-pain /
expected-move / what-if** from the **freshest TODAY `gamma_analyze` briefing** (the 4×/day Claude
briefing — one Claude writes the gamma thesis, the Driver's Claude now reads it) paired with a **live
per-index spot** (`fetch_market_context` gained `SPY,QQQ` → a fresh spot-vs-flip **posture**; the
briefing spot is the fallback), the **market-dashboard breadth (`$ADVN-$DECN`) + risk-on/off**
(`cache:market:dashboard`), and the **sentiment 0-10 score + bias** (`cache:sentiment:composite`
`live.composite`). **CONTEXT ONLY — no new hard rule**: `_market_read` is appended in `build_packet`
exactly like the existing `market_state` line (never filters the menu; absent sources → no key →
byte-identical to today), and **`guardrails.py` is UNTOUCHED** (the wall-aware rejection + breadth
halt are a **deferred** ③-gate follow-up that must be backtested first). Pure, defensive helpers in
`driver_svc/compute.py` (`_dashboard_risk_read` / `_pick_latest_briefing` [drops prior-session
briefings — stale walls mislead] / `_market_read` / `_posture` / `_as_of` / `_market_read_summary`);
the handler reads the caches into the `market` dict (`_read_briefing` across the 4 scheduled slot keys
+ `_read_sentiment_magnitude`); the decider `_SYSTEM` gained a paragraph on **how to weigh** the read
(prefer put-credit below the put wall / call-credit above the call wall; below-flip / risk-off →
be **selective, NOT stand down** — keeps the Very Aggressive $500/day mandate); and a one-line
`market_read` **summary is surfaced on each `/driver` decision-log row**. Driver stays **Redis + one
proxy quote** (3-tier clean — no engine/DB imports). Built directly, TDD, per-task commits: driver_svc
**170** + contracts **38** green. **Restart `driver_svc`** (benefits from `options_svc` + `market_svc` +
`sentiment_svc` up so the briefing / dashboard / composite caches populate). PAPER ONLY. Design/plan:
[design](docs/plans/2026-07-08-driver-market-context-block-design.md) /
[plan](docs/plans/2026-07-08-driver-market-context-block-plan.md). Prior — 2026-07-08 (**Removed the legacy morning-agent / order-approval queue (full purge)**
— per the user's directive, the entire legacy morning-agent + approval-queue subsystem was deleted in
three reviewed units. (1) **`services/driver_svc`** — the `run_morning`/`execute`/`build_perf_report`
compute, the `run`/`approve`/`skip`/`perf` handlers + `cache:driver:approvals`/`cache:driver:performance`,
the `morning_due` scheduler branch, and the `ApprovalState`/`PerfReport` contracts. The AUTONOMOUS path is
UNTOUCHED — its `fetch_market_context` was made **self-contained** (a direct `$VIX,$SPX,$VIX1D` proxy
fetch, defensive → `{}`) so it no longer imports `morning_agent`. (2) **`webgui/pages/driver.py`** — the
"Legacy approval queue" UI, "Run morning agent" button, approval cards/dialog + dead builders removed; the
page is now purely the **autonomous monitor + the closed-trade Performance view**. Stray refs cleaned in
`main.py` (nav badge), `status.py` (freshness row repointed to `driver:autonomous`), `eod.py` (driver tiles
repointed to `driver_paper_perf`), `page_help.py`. (3) **`claude-driver/`** — DELETED `morning_agent.py`,
`order_executor.py`, `trade_selector.py`, `perf_report.py`, `approval_server.py`, `order_preview.py`,
`intraday_monitor.py`, `start_all.bat` + their 8 tests; KEPT `config.py` (autonomous still reads
`RISK_LIMITS`) + `feature_engineer.py` (shared ML-feature builder, used by non-morning-agent ML scripts);
`tools/check_env.py` dropped the :8300 approval-server health check. Green: driver_svc **144** + contracts
**37** + webgui **679**; claude-driver introduced no new import errors. Branch `Using_Highcharts`.). Prior — 2026-07-08 (**Driver Performance view — real closed-trade P&L (was always $0)**
— the `/driver` "Performance" table read the dead legacy morning-agent `trade_log.json` ledger,
whose old "polled" equity/futures rows never close, so it showed **all-open, $0, ~2-week-stale**
data. Repointed it to the driver's **isolated paper account's `closed_positions`**
(`cache:options:driver_paper_account`) — the REAL closed options credit spreads with actual
realized P&L, updated every 5-min manage cycle + the page's 2s version-poll (timely). New PURE
builders in `webgui/pages/driver.py` — `closed_summary_text` (Closed N · W–L · win% · realized $)
+ `closed_trade_rows` (newest-first) + `_CLOSED_COLS` — with reader-friendly columns
(Closed/Symbol/Strategy/Qty/**humanized** Exit-reason [`TARGET_HIT`→"Target hit"]/colored Realized
P&L), dropping the useless legacy Bucket/Source/Status columns; a **Refresh** button forces an
immediate `driver_paper_manage` reprice. The legacy `cache:driver:performance` page read is gone.
webgui driver **31** green (compile-verified; browser check skipped — the running webgui holds
:8500). **Restart the webgui** to see it. Branch `Using_Highcharts`.). Prior — 2026-07-08 (**Driver "Very Aggressive" risk profile** — loosened the
autonomous driver's risk envelope toward the $500/day goal (user directive); all knobs now live
in `driver_svc/settings.py`: `PER_TRADE_MAX_RISK 1500→3000`, `DAILY_RISK_BUDGET 4500→12000`,
`MAX_CONCURRENT 6→10`, `MAX_TRADES_PER_CYCLE 3→5`, `VIX_MAX 25→35`, `MENU_TOP_N 12→15`, plus a NEW
`DAILY_LOSS_HALT=1500` — the biggest brake, replacing the legacy $250 halt that ended the day
after ONE losing $SPX (`compute._daily_max_loss` reads settings first, legacy `config.RISK_LIMITS`
as fallback). The paper OPEN path's `options_svc.compute._DRIVER_MAX_RISK_PER_TRADE`→`3000` (the
MANUAL account stays `config_paper.MAX_RISK_PER_TRADE=250`); the `decider._SYSTEM` prompt was
rewritten from "standing down is encouraged" to an AGGRESSIVE mandate (take reasonably-scored
trades to build toward the target; stand down only on genuinely poor edge / hostile conditions).
Net posture: ~half the $25k paper book deployable, ~12%/trade, a $1,500 daily-loss stop (3× the
target) — deliberately aggressive (user choice; dial back in `settings.py`). driver_svc **168** +
options_svc driver-account **15** green. **Restart driver_svc + options_svc** to pick it up.
Branch `Using_Highcharts`.). Prior — 2026-07-08 (**Gamma briefing history — store + CLI utility +
in-app viewer**: every Gamma Analyze briefing (the 4×/day Auto briefings + ad-hoc/
manual runs) is now **persisted** to a new SQLite store
`options-scanner/gamma_briefing_history_db.py` (`repo_paths.GAMMA_BRIEFING_DB`, one
row per `(date, slot)`) as its **STRUCTURED analysis payload** — the report HTML is
**regenerated on demand** (pure `compute.analyze_history_doc`), never frozen, so old
briefings re-render in the current infographic design and the data stays queryable
(bias/headline pulled out as columns). `handlers._persist_briefing` records each
successful run (wired into `run_scheduled_gamma_analyze` + the ad-hoc `gamma_analyze`
command; degraded no-chains/no-key pages have no `analysis` and are skipped);
`publish_gamma_briefing_index` publishes the metadata index
**`cache:options:gamma_briefings`** for the picker (startup + after each persist); the
**`gamma_history`** command regenerates a date's (or a single slot's) report →
**`cache:options:gamma_history`** → served raw at **`/options/gamma-history`**. A
**CLI utility** `services/options_svc/gamma_briefing_report.py` (run manually) does
`--list` / `--date [--slot]` / `--range START END` / `--generate` (fresh run → store
→ report; needs the proxy + key), writing HTML under
`options-scanner/data/gamma_reports/`. The `/options/gamma` page gains a **History
picker** (a date + slot dropdown from the index + an **Open** button that enqueues
`gamma_history` and opens the regenerated report in a new tab, mirroring
`_watch_analyze`; `history_dates` is the pure date-list helper). **Restart
`options_svc`** so persistence + the index publish go live. gamma_briefing_history_db
**7** + options_svc handlers/scheduler/compute + webgui **689** green. Built with
per-layer TDD. Branch `Using_Highcharts`.). Prior — 2026-07-07 (**Five-state market classifier (direction × aggression) —
Phases 0–5 + Tier 3 shipped (Phases 0–3 LIVE on REST data; Phases 4–5 streamer login+subscriptions
verified live, RTH order-flow population pending; Tier 3 = validation harness + LOW-weight swing/driver
integrations)**: the app's one-axis intraday
trend state (`scoring/intraday_trend.py:score_to_state` → `bull_trend`/`pullback_in_bull`/`range`/
`bear_rally`/`bear_trend`) is **replaced — for the regime-driving intraday state** — by a
**two-axis direction × aggression classifier** emitting five trader states: **Bullish / Lack of
Bullishness / Neutral / Lack of Bearishness / Bearish**. The two middle states capture the
effort-vs-result asymmetry a single directional axis can't express (price up but hollow → *Lack of
Bullishness*; price down but no follow-through → *Lack of Bearishness*). **Architecture:** the
existing 0–100 intraday trend score is the DIRECTION axis (unchanged), crossed with a NEW signed
**AGGRESSION** axis via a 9-cell grid (`sentiment-dashboard/scoring/market_state.py:classify_market_state`,
PURE; bands `≥60` bullish / `≤40` bearish, aggression `≥0.2`/`≤−0.2`). Aggression inputs
(confidence-weighted-blended via the PURE signed `scoring/aggression.py:blend_aggression`, graceful-
degrading): **(1) volume-effort** (`scoring/effort.py` — up/down-day volume ratio + volume-on-
rallies-vs-pullbacks + close-location-value over SPY daily); **(2) 25-delta risk-reversal skew Δ**
(`options-scanner/flow_skew.py`, computed in the options_svc **2-min GEX poll** from the ALREADY-
fetched $SPX/SPY/QQQ chains — no extra fetch — stored per snapshot in `gex_history_db`, published
as **`cache:options:flow_skew`** with `rr_delta` vs the prior snapshot; a shared-front-expiration
guard keeps the RR tenor-consistent); **(3) cross-sector cap-weighted P/C 5-trading-day Δ**
(`live_composite.cap_weighted_pcr` + a NEW daily store `services/sentiment_svc/sector_pcr_history_db.py`).
Wired in `sentiment_svc.compute_intraday_trend` (reads `cache:options:flow_skew` + `compute.sector_pc_delta()`,
signs+normalizes — **rising put demand → NEGATIVE aggression**, SCALE tunables `SKEW_DELTA_SCALE=5.0`
IV-pts / `PC_DELTA_SCALE=0.3` P/C — blends, classifies), threaded through the EXISTING
`trend_regime.commit_state` 2-day hysteresis with a **migration guard** (an old-vocab persisted state
is treated as cold-start so no stale string is published). Published under the **SAME** bridge
`trend_regime.state` key, so **`regime_filter` was rekeyed via its one `_TREND_STATE_VOTE` dict** to
the new vocab (`bullish`→bull/block-CCS · `bearish`→bear/block-PCS · `neutral`→None ·
`lack_of_bearishness`→lean_bull [resilient, puts undefended → favor PCS] · `lack_of_bullishness`→
lean_bear [exhaustion at highs → favor CCS]) — **`evaluate_regime`'s AND-of-agreement logic is
UNCHANGED** (the two middle states land exactly on the old soft-lean slots). `compute._bridge_trend`
always emits new-vocab (neutral at cold start) so the gate is NEVER fed an unrecognized string. The
daily committed state is **recorded** (`services/sentiment_svc/market_state_history_db.py`, 90-day
window) for a later backtest-validation task. `/sentiment` shows the five-state label + description +
a **"Why" evidence** popup (direction/effort/skew/flow/aggression lines) on the **Today** trend gauge;
the **30-Day structural gauge KEEPS the old band vocabulary** (a structural direction-only read has no
aggression axis — `score_to_state` is **retained, deliberately NOT deleted**), so the page carries
both vocabularies (`_TREND_SHORT`/`trend_text_class` cover all 10 keys). **Phase 0** lifted the
Telegram/Discord/Fi-SMS channel senders + `shared/notifications.json` config out of
`options_svc/push_notify.py` into a shared **`shared/notify/`** helper (for the coming state-transition
alerts). **Phase 3 (SHIPPED)** added three intraday structure signals — **session-structure**
(`scoring/session_structure.py`, VWAP-hold + opening-range break → blended into the DIRECTION
price sub-score, `SESSION_BLEND=0.20`), **rejection/defense** (`scoring/rejection_defense.py`,
upper-wick exhaustion at highs vs defended-dip resilience → a new `rejection` AGGRESSION component,
`AGG_WEIGHTS["rejection"]=0.20`, no sign flip), and **volume-profile-shape**
(`scoring/profile_shape.py`, balanced single-HVN session → damps aggression toward Neutral,
`PROFILE_DAMP=0.5`) — all folded into `compute_intraday_trend` (each defensive/degrading) — plus a
**state-transition phone push** (`services/sentiment_svc/state_alert.py`: on a committed-state FLIP,
fire Telegram/Discord/Fi-SMS via the `shared/notify/` helper, gated enabled + valid-new-vocab + differ
+ market-hours; the cold-start old→new-vocab first cycle and same-state are skipped; best-effort, can't
abort the recompute). **Phases 4–5 (SHIPPED — streamer equity + option aggressor flow; code-complete,
pending a LIVE RTH verification):** the aggression axis now has real order-flow. **Proxy (additive,
proven-safe):** `_normalize_level1_equity` widened with bid/ask/bid_size/ask_size/last_size/total_volume
(+ RTH `REGULAR_MARKET_*` fallbacks for last/last_size — resolves the old `TODO(live)`); a NEW
`_normalize_level1_option` (last/last_size/bid/ask) + a `/stream/options` SSE fan-out with a refcounted
OSI union on the EXISTING shared stream worker — **provably isolated from paper-trade tracking**: the
reconcile subscribes `_registry.legs_union() ∪ flow_osis` (replace-semantics, read fresh on the stream
loop) and the trade-untrack orphan guard spares `_option_refcount`, so a tracked leg can NEVER lose its
subscription; the trade-detector block in `_on_option_message` is byte-identical (fan-out appended after).
**Consumers (`services/sentiment_svc/order_flow_consumer.py`, mirror the portfolio SSE-worker pattern):**
an EQUITY worker streams `/stream/quotes?symbols=SPY,QQQ`, classifies each trade via the PURE
`scoring/order_flow.py` (Lee-Ready quote rule + tick test → aggressor ratio / CVD), rolls a 5-min window;
an OPTION worker refreshes near-ATM SPY/QQQ OSIs every 5 min, streams `/stream/options`, classifies
put/call trades at bid/ask (per-OSI prev_last) → a signed put/call-pressure `signal` (put-buying →
NEGATIVE → bearish); both publish into **`cache:sentiment:order_flow`** (`{SPY,QQQ, options}`). The
classifier folds SPY equity CVD as the **`order_flow`** component (weight 0.15) and option pressure as a
distinct **`option_flow`** component (weight 0.10) — both NO sign flip (positive = net buying = bullish =
aligned), both defensive/degrading (no stream → drop out). Honest caveat: level-one CONFLATES rapid
ticks, so this is a **sampled** read (reliable over minute windows, not tick-perfect); Schwab has no
time-&-sales, SPY proxies $SPX (no index tape). **Still needs a LIVE RTH check** (restart proxy +
sentiment_svc, watch `cache:sentiment:order_flow` populate + the aggression axis move) — the blocking SSE
workers are live-verified, not unit-tested (the pure classifier/window/aggregate helpers carry the
coverage, mirroring the portfolio precedent). **Tier 3 (SHIPPED — validate-first): item 11** built an
OFFLINE validation harness (`sentiment-dashboard/validate_market_state.py` — run manually, NEVER in a
request path) that reconstructs the daily committed state over ~5yr SPY history (a daily-OHLCV CORE
reconstruction: a NEW `scoring/daily_direction.py:daily_direction_score` proxy × the REAL
`effort`+`rejection_defense` aggression, through the REAL `market_state` grid + `commit_state`
hysteresis) and measures forward-return stratification (per-state mean/hit-rate + **ordinal IC**). **Honest
result:** 20d ordinal IC **+0.087** (5d +0.055) — a modest, **regime-dependent** edge (calm IC +0.086 /
stressed +0.024) CONCENTRATED IN THE TWO MIDDLE STATES (Lack-of-Bullishness +0.99% vs Lack-of-Bearishness
+2.16% mean-20d — the framework's effort-vs-result innovation); the extremes are **inconclusive** here
(Bullish +0.65% underperformed via exhaustion; **Bearish NEVER fired in 5yr** — the inputs that most drive
it, skew spikes + put-flow, are exactly the ones EXCLUDED from the daily reconstruction). So — like the
validated swing model — a thin, label-don't-overtrust edge. **Items 9 & 10 were therefore built at LOW
weight (user decision):** **item 9** = a SMALL bounded family-fit tilt (`strategy_scoring.state_family_tilt`,
`STATE_TILT_MAX=6`, leaning on the two middle states — Lack-of-Bearishness→PCS+, Lack-of-Bullishness→CCS+/
long-call−) applied to `score_strategy`'s composite **AFTER the hard-gate grade is decided (a ranking nudge
that can NEVER flip a gated grade)**, fed by the live state read from `cache:sentiment:composite` in the
`swing` handler; **item 10** = the committed state (label+evidence) surfaced to the **Driver's Claude
decider as CONTEXT ONLY** in `build_packet` (read in the driver handler) — **`guardrails.py` is UNTOUCHED**
(`regime_filter` already hard-gates the driver's menu; the state is context, not a second gate, proven
context-only by test). Both additive/defensive (no state → no tilt / no context line). Everything is
ADDITIVE except the ONE coordinated `trend_regime.state` vocabulary change (`regime_filter` rekeyed in
lockstep). Green: sentiment_svc **136**, options_svc **400**, driver_svc **168**, webgui **681**,
schwab-proxy **82** (equity + option stream fan-out), options-scanner flow_skew **+18** / strategy_scoring
**56** / gex_history migration, sentiment-dashboard scoring modules
(effort/aggression/market_state/session/rejection/profile/order_flow/daily_direction) **+101**,
shared/notify **14**.
Built subagent-by-subagent (TDD, two-stage spec+quality review per unit). **Restart `options_svc` +
`sentiment_svc`** to pick this up. Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-07-five-state-market-classifier-design.md) /
[plan](docs/plans/2026-07-07-five-state-market-classifier-plan.md).). Prior — 2026-07-06 (**Paper expiration auto-close (both books) + thrice-daily
"trades needing action" push** — two features. **(1) Expiration auto-close.** Validated that
paper trades did NOT reliably auto-close on expiration and fixed both stores. The **account**
engine (`paper_engine.run_manage_cycle`, `paper_account.db`) settles at intrinsic, but had two
bugs: it settled 0-DTE **intraday at the open** (the 5-min auto-manage tick made this fire at
08:30) and it **could never settle a past expiration** because
`signal_repricer.reprice_swing` returns `current_underlying=None` for `exp < today` (it skips the
doomed chain fetch). Now gated by the pure `paper_engine.should_settle(exp, today, now_ct)` —
settle at/after **15:00 CT** on the expiry day (4pm ET close) or any later day — with a direct
`paper_engine.underlying_last(client, symbol)` quote fallback when the repricer supplies none
(`run_manage_cycle` gained a `now_ct=None` param for deterministic tests). The **ledger**
(`paper_trader.py`/`trades.db`, the Paper Trades tab) **never auto-closed at all** —
`expire_paper_trade` had ZERO callers — so new `compute.expire_ledger_trades(now_ct=None)`
settles OPEN ledger trades on the SAME `should_settle` gate, wired into
`handlers.run_manage_and_refresh` (the 5-min manage tick + the manual "Run manage cycle" button;
the pre-existing piggyback `refresh_paper_trades` republishes the settled rows). See
[[paper-two-systems-expiration]]. **(2) Action alerts.** A thrice-daily push (Telegram + Discord;
SMS if configured) at **10:00 / 13:00 / 15:00 CT** on trading days summarizing **trades needing
action** — `scheduler.action_alert_due` (once per slot within a 20-min grace, mirrors
`analyze_slot_due`) → `handlers.run_action_alert(bus, slot)` → `compute.collect_action_items`
(four categories: captured signals recommending **CUT/TAKE_PROFIT** via a fresh `reprice_captured`,
**expiring-today** ledger+account trades, **at-risk** rescue tested/critical, account **near
stop/target** [40–50% of max profit, or 150–200% of credit loss]) → `push_notify.send_action_digest`
(new `action_digest_text`/`action_digest_embed`/`action_total`/`action_slot_label`; skips an empty
digest — no "all clear" spam). Cached at `cache:options:action_alert` for inspection. All defensive
+ per-category guarded. Restart `options_svc` to pick both up. options_svc **389** + options-scanner
paper/eod/repricer **71** green; verified live (digest built against real data: 17 captured actions +
1 at-risk). Branch `Using_Highcharts`.). Prior — 2026-07-02 (**Driver risk-sizing fix (RISK_TOO_HIGH) + Sonnet 5 + prompt
caching** — a debugging session on "driver trades logged **Executed** but never showed up."
Root cause: the `/driver` decision-log "Executed N: SYM×q" line is only the **enqueue** of a
`driver_paper_create` command; the real open in `options_svc.compute.open_driver_position` was
**silently rejecting** every $SPX/MU pick with `RISK_TOO_HIGH` (the truth is the account view's
rolling **`last_open_results`**, and the driver DB had NEVER held an $SPX or MU position). The
cause was a **100× units mismatch**: `guardrails.clamp_quantity` (driver_svc) sized affordability
off the scanner's **per-SHARE** `max_loss` (~$7) while `paper_sizing.size_contracts`
(options-scanner) correctly used **per-CONTRACT** dollars (`(width−credit)×100`, ~$705), so the
driver kept approving $SPX/MU whose real per-contract risk ($409–$1,833) blew past the paper
sizer's `config_paper.MAX_RISK_PER_TRADE=$250` → sized to 0. **Fixed:** (1) the guardrail now
evaluates **per-contract dollars** (`guardrails.CONTRACT_MULTIPLIER=100` + `_max_loss_dollars`)
in `clamp_quantity` + the daily-budget accounting; (2) the driver's caps raised
`PER_TRADE_MAX_RISK 300→1500` / `DAILY_RISK_BUDGET 900→4500` (user opted to let $SPX/MU trade);
(3) the paper OPEN path got its own `options_svc.compute._DRIVER_MAX_RISK_PER_TRADE=1500` (passed
explicitly to `size_contracts`) so the user's MANUAL paper account stays at $250. A $SPX
regression test (rejected at $250, opens 2 contracts at $1500) + updated guardrail/e2e/packet
tests pin it; driver_svc **160** + options_svc **334** green. **NOTE:** the legacy daily-loss
halt is still **$250** (`config.RISK_LIMITS`), so with $1,500 trades one losing $SPX can trip the
day's halt (raise it if undesired); the widest $SPX (~$1,833/contract) stays excluded at $1,500.
**Restart options_svc + driver_svc** to pick this up. See
[[driver-executed-but-rejected-risk-too-high]]. **Also this session:** both Claude API call sites
upgraded **Sonnet 4.6 → Sonnet 5** (`claude-sonnet-5`) — the driver decider (via the gitignored
`shared/driver_model.txt` override) + the Gamma Analyze `_ANALYZE_MODEL`; live-probed first
(Sonnet 5 **accepts** `thinking:{"type":"disabled"}`, unlike Fable 5, so no param rework), build
default stays **Opus 4.8**. **Prompt caching** enabled on the driver decision call
(`decider._cached_system` cache-marks the tools+system prefix, 1h TTL to match the 30-min
checkpoint cadence) — currently **inert** (the ~800-token prefix is below Sonnet's 2048-token
cache floor, so nothing is cached or billed extra; engages automatically if the static prefix
grows). Branch `Using_Highcharts`.). Prior — 2026-07-01 (**Reliability remediation** — the technical audit's
[Reliability & Error Handling](docs/audits/2026-07-01-technical-audit.md) pillar (the lowest-scored,
5/10) addressed; theme = *keep the "never raises" defensiveness, add the evidence*. All suites green:
options_svc **333**, driver_svc **157**, sentiment_svc **61**, portfolio_svc **32**, trade_svc **68**,
shared/contracts **42**, scaffold **20**, proxy **63**, webgui **676**, options-scanner paper/scoring
modules **90** (full options-scanner blocked only by the pre-existing intermittent tkinter dashboard
crash; the OPTS agent's clean run was 1186/10-baseline). **R1 (flagship — retires the known days-long
silent-KeyError incident):** `options_svc/handlers.py` now **captures** `open_driver_position`'s result,
logs opened/rejected/error, and surfaces a rolling `last_open_results` (cap 25) on
`cache:options:driver_paper_account` so the /driver log shows per-trade OUTCOMES, not just "enqueued".
**R2 (dead scheduler was invisible):** `_scaffold._supervise_scheduler` restarts a dead scheduler
coroutine (3 s backoff, `max_restarts=10` storm cap → then `alive=False`) and `/health` gains
`scheduler_alive`/`scheduler_restarts`/`scheduler_last_tick_age_s` (the age is "since last (re)start" —
`scheduler_alive` is the load-bearing signal; a service with `scheduler=None` reports alive). **R3
(no persistent logs + silent excepts):** `make_app` installs a per-service `RotatingFileHandler`
(`services/<domain>_svc/logs/<domain>.log`, 10 MB × 5, root logger, **off under pytest**, idempotent);
**19 scheduler + several handler/compute `except Exception: pass`** → `log.exception`. **R4b/R8 (failures
invisible until /status):** the app-wide watcher (`webgui/main.py`, on every page) now alerts (chime +
`⚠` Status nav badge + optional desktop notification, same settings/market-hours gate) on **STALE
scheduled views + down service `/health`** — pure transition-deduped logic in `alerts.py`
(`new_health_alerts`), service-health probe throttled to 30 s (not every 2 s). **R5 (stale trade
commands):** additive **`Command.ts`** (`shared/contracts`) + a 3-min staleness gate rejects stale
`driver_paper_create`/`paper_create` (missing ts → treated fresh, back-compat). **R6 (non-atomic open
→ BP drift):** `paper_account_db.reconcile_buying_power` recomputes reserved BP = Σ open positions'
max_loss (keeps `cash+reserved` invariant), run at options_svc scheduler startup for BOTH the manual +
driver books. **R7 (stand-down reason opaque):** `decider` classifies `no_key`/`api_error`/
`parse_error`/`model` (fail-safe behavior byte-identical — additive), carried through
`_publish_autonomous` onto the decision-log row → the /driver UI shows a red incident chip so a broken
API key looks like an ops incident, not model caution. **R9 (Low):** proxy stops retrying deterministic
4xx (401-refresh + order-POST-no-retry intact) + rotates its INFO log; portfolio SSE gets capped
backoff + logging; the app-wide `_tick` is `guard_async`-wrapped + logs once on a bus outage (not a
traceback every 2 s); EOD archives write via temp-file + `os.replace`. **DEFERRED (flagged):** **R4a**
— a cross-process auto-restart **supervisor daemon** (R2 in-process restart + R4b alerting already cover
visibility + in-process self-heal; a standalone watchdog that auto-restarts dead PROCESSES is new
always-on machinery, offered as an optional follow-up). Remaining audit pillars: **Security** (proxy
wildcard CORS + no-auth order path, Memurai password, dep pinning) + **Code Quality** (god-modules,
`render()` closures, sys.path/collision debt). Prior — 2026-07-01 (**Performance + Architecture remediation** — the technical audit's
[Performance & Speed](docs/audits/2026-07-01-technical-audit.md) + [Scalability & Architecture]
pillars addressed; all suites green: options-scanner **1181** [+11 pre-existing baseline], options_svc
**322**, driver_svc **143**, sentiment_svc **61**, portfolio_svc **29**, shared/bus **20**, scaffold
**8**, webgui **658**). **P3 (command handlers off the event loop):** `services/_scaffold.py`'s
consume loop now dispatches each command via `run_in_executor` (one-at-a-time, read order) so a slow
handler (a ~19 s `sim_fetch`) no longer stalls `/health`, the scheduler, or the queue. **A2 (command-
stream hygiene, `shared/bus/client.py`):** `enqueue_command` XADD is bounded (`_XADD_MAXLEN=1000`,
approximate); a **dead-letter** convention `cmd:{domain}:dead` (Redis list, `Bus.dead_letter`/
`dead_letter_key`) + per-entry decode: a handler that raises → dead-letter + ack (was: logged &
discarded); an undecodable/poison entry → dead-letter + ack + **batch continues** (was: whole batch
failed un-acked into the PEL forever); `Bus.drain_pending` (`XAUTOCLAIM`, min-idle 0) drains a crashed
consumer's stranded PEL to the dead-letter list at startup — **surfaced for review, never silently
lost NOR blindly re-executed** (a stranded `driver_paper_create`/`rescue_apply` re-run could double-
open). **P1/A1 (GEX retention):** `gex_history_db.purge_keep_sessions(keep=5)` now deletes old rows
from BOTH `gex_snapshots` AND `gex_term_snapshots` (the term table previously had NO purge), called
from `compute.collect_gex_snapshots` **at most once per local date** (`_LAST_PURGE_DATE` latch, not
every 2-min tick) — bounds the ~3 GB DB growth while keeping the last 5 sessions so the off-hours
persistence still works. DELETE reuses free pages but doesn't shrink the file: a **one-time manual
`VACUUM`** (`PRAGMA auto_vacuum=INCREMENTAL; VACUUM;`, run offline) is documented to reclaim the 3 GB
— deliberately NOT auto-run (locks the live DB for minutes). **P2 (slim `cache:options:gamma`):**
`compute.gamma_snapshot` now crops every view's per-strike history grid to the ±20-strike display
window (`GAMMA_N_SIDE`, widened for the intraday spot path) BEFORE caching — **flip/walls are still
computed on the FULL grid first** (crop-invariant; verified a far $SPX wall at 3000 survives). Same
key/structure, so the page is unchanged; measured **16.3 MB → 3.07 MB ($SPX), 9.8 MB → 2.97 MB (SPY)**
on a trending day (~1 MB calm). **P5/P6 (webgui off-loop reads):** the big gamma + calc-chain payload
reads now run via `nicegui.run.io_bound` under `guard_async` + an in-flight guard (the cheap `:ver`
version probes stay on-loop; version-gating preserved so the 14 MB isn't fetched every 2 s). **A4
(scheduler concurrency):** `options_svc/scheduler.py` runs the due slot branches concurrently so a slow
15-min rescan can't delay the 2-min GEX collect / 5-min manage (per-branch isolation preserved). **P4
(sentiment cost):** the 120 s refresh is off-hours-gated (`refresh_due`) and the 35-day backfill is now
computed **at most once per session-day** (`_load_snapshots_cached`) with `skip_unchanged` on the
history publish — ~95%+ fewer off-hours proxy calls, RTH cadence unchanged. **P8/P9 (GEX):** sargable
`last_snapshot_age`/`first_snapshot_today` (`ts >= ? AND ts < ?` range) + one reused read-only
connection across the 4 gamma views. **P10 (portfolio):** the 10-min full rebuild is off-hours-gated
(explicit refresh still immediate). **DEFERRED by decision** (user-confirmed): **A5** (per-tab request-id
result keying — a single-user-multi-tab edge case) and **A6** (retire the `sentiment_bridge.json`
dual-write → regime_filter reads Redis; + the ':8100 proxy may be the source repo's binary' version-skew)
— the bridge retirement is a live-scanner-gating migration and the proxy-skew is an ops concern; both
left for a dedicated pass. Reliability + Security + Code-Quality pillars are the remaining audit
follow-ups. Prior — 2026-07-01 (**Calculation-accuracy audit + remediation**: a five-domain
quant audit of the app's math [full reports under [`docs/audits/`](docs/audits/):
[technical audit](docs/audits/2026-07-01-technical-audit.md) +
[calculation-accuracy audit](docs/audits/2026-07-01-calculation-accuracy-audit.md)] found the
money-bearing math (BSM pricing/Greeks/IV solver, expected move, defined-risk trade economics,
buying-power/margin, GEX regime signals, the look-ahead-free factor model) **textbook-correct**,
but flagged a set of standard-conformance + consistency defects, **now FIXED** (all suites green:
options-scanner **1166** [+10 pre-existing baseline fails], options_svc **314**, trade_svc **68**,
sentiment_svc **52**, portfolio-analyzer **198**, portfolio_svc **27**). **Behavior changes to know:**
(1) **RSI + ADX now use Wilder's RMA smoothing** (`shared/analysis_lib/technical.py` +
`trade_svc/compute.py`), not simple rolling means — values now match TOS/TradingView (RSI-14
validated against the StockCharts worked example 70.53/66.32); this shifts the Trade-page momentum
strip + the sentiment intraday-trend needle (correctly). (2) **VWAP is now session-anchored**
(resets each session), not a multi-day cumulative. (3) **Volume-profile value area** now grows
**contiguously from the POC** (standard Market-Profile), not by sorting disjoint high-volume bins.
(4) **Relative Strength** (`technical.calculate_relative_strength` + `analysis_lib/sector_analysis.py`
Holdings "vs Sector (RS)") switched from an unstable return-ratio [sign-inverted in down markets] to
a **parity ratio `100·(1+stock)/(1+bench)`**. (5) **Swing-scanner economics are now
commission-aware**: a new PURE `options-scanner/commissions.py` (reads `config/commissions.toml`,
no `services/` import) folds **round-trip commission** ($0.65/leg × legs × 2) into
`strategy_scanner.payoff_metrics`' `max_profit`/`max_loss`/`capital` [never off an unbounded profit],
so R:R + capital-efficiency + the quality **grade** are net-of-fees — a borderline IC can now flip
Good→Weak. **Driver-facing gap CLOSED:** the live autonomous **driver sizes from the FLAT scanner**
(`cache:options:scan`), not the swing scanner — so rather than mutate the flat scanner's tuned
composite score / sort / paper-BP sizing (all consume the gross `credit`/`max_loss`/`rr_pct`),
`scanner_engine._attach_net_economics` adds **additive** `commission`/`net_credit`/`net_max_loss`/
`net_rr_pct` to every PCS/CCS/IC signal, and the driver's model menu (`driver_svc.compute._menu_item`
+ the decider system prompt) now shows the model the **net** credit/max_loss + commission, so the
driver's perceived edge is net-of-fees while scoring/ranking/sizing + the webgui display stay
untouched (guardrail BP sizing still keys off the raw gross `max_loss` — structural margin, not
commission — by design). Additionally, the **paper engine now debits commission into realized
P&L at close** (`paper_engine.net_realized_pnl` → both close sites; round-trip on a managed
BUY_TO_CLOSE, opening-only on an OTM expiry), reducing both the stored `realized_pnl` and account
cash from the one value in `_close` — so the **driver performance scorecard AND the manual paper
account are net-of-fees** (the rescue-apply close path already did this). (6) **Swing payoff units
normalized to per-CONTRACT dollars (×100)** across all families (`payoff_metrics` native builders
were per-share while credit adapters were ×100 — now consistent; `_normalize_credit` `capital` bug
fixed: `capital = max_loss` for defined-risk credit). (7) **Single risk-free-rate source**
`options_calculator.RISK_FREE_RATE = 0.045` (was 0.045 in the calculator vs **0.04** in the
simulator); **`q = 0` dividend assumption documented** in the BSM docstrings. (8) **Simulator
expiry settlement fixed** from a timezone-naive `hour=15` to **16:00 US/Eastern tz-aware**,
matching the calculator (`options_calculator.expiry_time_to_years`) — the 0DTE bug where 15:30
collapsed an option to intrinsic-only ($0.012 vs the correct $0.090). (9) **Term-structure GEX
×0.01 unit fix** (`gamma_tool.compute_term_grid`) — term cells were **100× the intraday scale**
(per-$1² not per-1%); GEX magnitudes documented as **nearest-expiry-relative** (not a full-surface
SpotGamma replica — sign/flip/walls ARE standard). (10) **Portfolio annualized return** switched to
a **252 trading-day basis** (was calendar 365 while vol used √252 → ~1.45× Sharpe-scale error;
`evaluation.py` now `busday_count`-based). (11) **Factor-model live scorer z-basis** now matches
the fit's **2/98 cross-sectional winsorization** (`swing_model.py`, was a ±3 hard clip → mild tail
miscalibration; ±3 kept only on the thin-snapshot norm fallback). (12) **Two PoP conventions
documented** (calculator = risk-neutral lognormal r-drift; swing = zero-drift normal) — labeled,
not unified. **DEFERRED (require a manual `fit_swing_model.py` refit against live 5-yr proxy data —
documented in-code in `swing_model.py` + the audit doc):** covariance-aware factor weighting [the
univariate signed-IC weighter double-counts the correlated momentum cluster] and regime-gating
`low_vol`'s regime-overfit inverted sign. Reliability/security/perf/architecture findings from the
same audit pass (silent-degradation logging, 3 GB unbounded `gex_history.db`, at-most-once command
streams, proxy wildcard-CORS) are catalogued in the technical-audit doc as OPEN follow-ups. Branch
`Using_Highcharts`. Prior — 2026-06-30 (**Swing Scanner — quality-gated grading**: the multi-strategy
Swing Scanner's **grade now reflects trade QUALITY, not view-fit**. The `score_strategy`
composite is **quality-dominant** (`0.7·quality + 0.3·fit` — fit demoted to a ranking
tiebreaker), and the **grade is capped by per-family HARD GATES** (liquidity / R:R-or-
capital-efficiency / PoP): a trade failing any minimum bar → **Weak** + a **`grade_reason`**
naming the failed dims ("Fails: liquidity, PoP"); pass mins → Good/Marginal; clear the
**excellent** bars on every gated dim + composite ≥78 → **Strong** (genuinely rare). Per-family
bars (credit = high-PoP/low-R:R; long = low-PoP/high-R:R with unbounded-profit auto-passing
reward; naked = capital-efficiency → below Strong by design). Making the **liquidity gate real**
required carrying `bid`/`ask`/`volume`/`oi` onto the normalized legs (`strategy_scanner` +
`scanner_engine.build_iron_condors` now forwards both IC shorts' liquidity). The page shows a
**color-coded Grade** (Strong/Good→green, Marginal→amber, Weak→red) with the reason in a tooltip.
strategy_scanner **26** + strategy_scoring **57** + options_svc + webgui **653** green;
live-verified (SPY/NVDA/IWM: Weak trades carry a liquidity/R:R reason, Strong rare, quality
dominates so a counter-view but structurally-sound trade can still grade Good). Design/plan:
[design](docs/plans/2026-06-30-swing-quality-gated-grading-design.md) /
[plan](docs/plans/2026-06-30-swing-quality-gated-grading.md). Prior — 2026-06-30
(**Multi-strategy Swing Scanner — Phase 1**: the `/options/swing`
page was expanded from a credit-spread-only premium scanner to a **unified single-symbol
multi-strategy scanner** — it builds + ranks candidates across **Directional** (long/naked
call+put), **Spreads** (debit bull-call/bear-put + credit PCS/CCS), and **Neutral** (iron
condor) families on ONE comparable **0–100 Fit+Quality score**. The scanner **infers a market
view** (direction/conviction + IV vol-regime) from the symbol's own technicals + IV and scores
each structure by FIT-to-that-view + STRUCTURAL-QUALITY (because the legacy `scoring.py` is a
premium-seller's model that would rank long calls/debit spreads near zero). Two new PURE engine
modules — `options-scanner/strategy_scanner.py` (normalized-signal builders + a structure-driven
`payoff_metrics`: analytic `unbounded` flag from the call-tail coefficient, breakpoint extrema,
PoP) + `strategy_scoring.py` (`infer_market_view` + `fit_directional`/`fit_vol` + quality
normalizers + `score_strategy`/`score_all`) — feed `compute.swing_scan` (now returns
`{signals, view}` + a `families` arg; derives `atm_iv` decimal from the engine's dollar daily EM;
adapts the existing `screen_spreads`/`build_iron_condors` output into the normalized shape). The
page gains a families multiselect + an inferred-view banner + strategy-agnostic colored columns
(`strategy_table.py`), with legs-based Calculator/Expected-Move handoff for all types (Paper-trade
gated to credit structures). strategy_scanner **18** + strategy_scoring **35** + options_svc **313**
+ webgui **650** green; live-verified end-to-end against the proxy (SPY/NVDA → inferred bearish
view, BEAR_PUT/LONG_PUT correctly ranked top). Phases 2 (condor/butterfly/iron-fly) + 3 (diagonals)
planned. Built subagent-by-subagent (TDD, two-stage spec+quality review per unit). Branch
`Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-30-multi-strategy-swing-scanner-design.md) /
[plan](docs/plans/2026-06-30-multi-strategy-swing-scanner.md). Prior — 2026-06-30
(**Sentiment "Daily Sentiment & Trend" intraday graphs**: the
`/sentiment` page's collapsed **30-Day History** section — the daily composite-history line
+ 5d/20d rolling-average + velocity/divergence text — is **replaced** by a collapsed
**"Daily Sentiment & Trend"** expander holding **two stacked, value-colorized (green/yellow/red)
2-min intraday graphs**: Daily Market Sentiment (0–10) + Daily Market Trend (0–100). Each is a
Highcharts line colorized by value via `series.zones`/`zoneAxis:"y"` over an **ordinal datetime
x-axis** (collapses overnight session gaps), rolling the **last 5 trading days**. The series is
**recorded going forward** (no backfill) — `sentiment_svc`'s 120 s `refresh()` records one
`(ts, sentiment 0–10, trend 0–100)` point, **RTH-gated** (Mon–Fri 08:30–15:00 CT), into a new
SQLite store (`services/sentiment_svc/intraday_history_db.py`,
`repo_paths.SENTIMENT_INTRADAY_DB = sentiment-dashboard/data/sentiment_intraday.db`; rolling
window = last 5 distinct local dates; one shared connection `check_same_thread=False` serialized
by `handlers._INTRADAY_LOCK` across the multi-worker executor), prunes to 5 days, and publishes
**`cache:sentiment:intraday_history`** (`{"points":[{ts,sentiment,trend},…]}`; additive
`shared/contracts/sentiment.py:IntradayHistory`). The page (`webgui/pages/sentiment.py`) reads
that view in `_read_cache` (it rides the composite version bump — same refresh cycle), paints
both charts in `_apply` via the PURE builders `build_sentiment_intraday_figure` /
`build_trend_intraday_figure` (sentiment bands ≤4.5/≤6.5, trend bands ≤30/≤70, matching the
gauge/`score_to_state` semantics), and **reflows on expand** (a `@guard`-wrapped worker — a chart
built inside a collapsed expander measures 0×0, the documented Simulator-hidden-tab fix).
sentiment_svc **51** + shared/contracts **39** + webgui **617** green; live-verified end-to-end
(restarted service created the DB + recorded a real RTH point → page rendered both colorized
charts, session gap collapsed, no console errors). Built subagent-by-subagent (TDD, two-stage
spec+quality review per task). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-30-sentiment-daily-intraday-graphs-design.md) /
[plan](docs/plans/2026-06-30-sentiment-daily-intraday-graphs.md). Prior — 2026-06-28
(**✅ Tailwind-first UI migration COMPLETE (Phases 0–8) — the
ENTIRE webgui is Tailwind-only**: all NiceGUI component styling now uses **Tailwind utility
classes via `.classes()`** — **zero `.style()`/`:style=` remain anywhere in `webgui/pages`**
(verified by grep + the `test_no_inline_style.py` guard over every page); **607 webgui tests
green; every page live-verified** in the browser preview. The dark-navy theme is a vocabulary of
**Python Tailwind-class-string token constants** in `pages/options/theme.py`
(`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/`BTN`/`BTN_PRIMARY`/`STRATEGY_BTN`/`TXT_*`/`BTN_3D*`)
applied with `.classes(CARD)`; the legacy `DASHBOARD_CSS` was **DELETED** (P4) — `theme.py` is now
**tokens + the one `QUASAR_INTERNAL_CSS` escape hatch** (field/tab/menu internals). Dynamic
data-driven colors are **palette-mapped** to fixed Tailwind classes (per-page local maps where the
palette is page-specific); genuinely-continuous values (e.g. a panel-flex ratio) use a runtime
arbitrary `flex-[…]` class. **Out of scope** (left as-is, by rule): Highcharts option dicts, raw
`ui.html()` HTML-string fragments + their CSS (EOD/Gamma Explain/Analyze), and Quasar `color=`
props. The ONE escape hatch is per-page **Quasar-internal** `ui.add_css` (table/field/tab/menu
internals). Scope **pragmatic**, intent **convert + light polish** (every page kept its existing
look). Built phase-by-phase (menu → each screen by logical group), each phase spec+quality-reviewed
by subagents, browser-gated, and tests-green — see the "UI styling standard — Tailwind-first" +
"App theme — dark-navy" sections below + the
[design doc](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md) /
[plan](docs/plans/2026-06-28-tailwind-first-ui-migration-plan.md) /
[phase2](docs/plans/2026-06-28-tailwind-first-ui-migration-phase2-plan.md) /
[phase3a](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3a-plan.md) /
[phase3b](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3b-plan.md) /
[phase3c](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3c-plan.md) /
[phase4](docs/plans/2026-06-28-tailwind-first-ui-migration-phase4-plan.md) /
[phase5](docs/plans/2026-06-28-tailwind-first-ui-migration-phase5-plan.md). **Phase 0
(token vocabulary) + Phase 1 (nav shell) + Phase 2 (shared `pages/options/*` helpers —
`.style()`-free, dynamic colors palette-mapped) + Phase 3a (the six signal-table screens
— Scanner/Swing/Captured/Paper/Paper-Portfolio/Rescue) + Phase 3b (Calculator + Simulator
on tokens) + Phase 3c (Gamma + Expected-Move; panel flex via a continuous-value runtime
arbitrary class) + Phase 4 (Trade + the LEGACY CLEANUP: `DASHBOARD_CSS` DELETED — `theme.py`
= tokens + `QUASAR_INTERNAL_CSS` only) + Phase 5 (Sentiment + Sector Rotation — the heaviest
~58 `.style()`; local color-class maps, sector-table CSS → Tailwind, auto-refresh recolors
via `remove/add`) DONE — OPTIONS + TRADE + SENTIMENT TAILWIND-ONLY**
— webgui 599 green + live-verified (no class-stacking under auto-refresh; gauges/charts intact). **Phase 3a** removes every `.style()` AND every Vue `:style=` slot binding
from those pages: dynamic Quasar table-cell colors now map to a stamped Tailwind **`:class`**
field from a finite palette (`score_zone_class`/`rec_class`/`pnl_class`/`verdict_class`/
`heat_bg_class`/`cash_class` — exact hexes preserved as `bg-[#..]`/`text-[#..]` arbitrary
classes; the shared `heat_border_class` in `rescue.py` is imported by captured/paper/portfolio
for the at-risk left-border tint, DRY); the 3D gradient buttons (Scanner "Run scan" + Paper
actions) move to shared **`theme.BTN_3D` / `theme.BTN_3D_DANGER`** tokens (Tailwind arbitrary
`bg-[linear-gradient(…)]` + multi-layer `shadow-[…,…]` + `hover:`/`active:` variants, applied
with `color=None`); each page's `ui.add_css` block is slimmed to Quasar-table-internal rules
only (cell `td/th` padding, sticky `thead tr th`, `.q-table__middle` max-height, the scanner
`.q-tab`/`.q-tab__indicator`/`.q-tab--active` chrome). A `test_no_inline_style.py` guard
asserts all six files are `.style(`/`:style=`-free. Live-verified (computed styles): the
gradient/multi-layer-shadow buttons render exactly, tab accents + active underline, score/
heat/rec badge bg colors, cash/P&L text colors, and the 3:2 rescue column split — no console
errors. Next is Phase 3b. Prior — **Validated swing (1–8 wk) evaluation —
Trade page**:
the `/trade` **Position** verdict's hand-weighted swing scoring is **replaced by a
backtested, IC-weighted cross-sectional factor model** (investing/months deferred —
needs point-in-time fundamentals). A new PURE factor library
(`trade-analyzer/src/analysis/factors.py`: 10 causal, sign-corrected, daily-OHLCV
factors + a registry — `mom_12_1`/`mom_6_1`/`pth`/`str_5d`/`vol_adj_mom`/
`trend_quality`/`low_vol`/`rs_spy`/`rs_sector`/`turnover`; no look-ahead — winsorized
**cross-sectionally at scoring**, not per-factor) feeds an OFFLINE harness
(`trade-analyzer/backtest.py` IC/ICIR/quantile-spread/`zscore_by_date`/**signed
IC-weighting**/`walk_forward`/`calibrate` + the orchestrator `fit_swing_model.py`,
**run manually — NEVER in the request path**) that pulls ~78 liquid symbols' 5-yr
daily history, builds a (date,symbol) panel with **20-day forward EXCESS-return-vs-SPY**
labels, and writes the versioned **artifact `trade-analyzer/data/swing_model.json`**
(signed weights + per-factor IC + cross-sectional norm + score→outcome calibration +
walk-forward OOS IC) + a markdown research report (both gitignored under `data/`). A
LIVE scorer (`services/trade_svc/swing_model.py`, on-demand in `analyze()`, defensive →
falls back to legacy/None) z-scores the symbol's current factors **CROSS-SECTIONALLY
against the current universe snapshot** (re-centered to today's regime — the
calibration-consistent basis; the artifact's time-averaged norm is only a thin-snapshot
fallback), **clips z to ±3**, composites with the signed weights, and reads
**BUY/SELL/HOLD off the calibration band** + a percentile
+ expected forward return + beat-SPY hit-rate. Additive optional **`swing_model`** block
on `TradeAnalysis`; the daily `cache:trade:universe_factors` snapshot — built over the
artifact's **`fit_universe`** (~78-name fit cross-section) — is the scoring basis (the
time-averaged norm is the thin-snapshot fallback). The `/trade` Position card shows the validated verdict as the headline +
a calibrated outcome line + a **"Why — validated factors"** evidence expander, with the
**legacy heuristic** verdict tucked into a collapsed expander (Investor + Markov cards
unchanged — the **Markov card still forecasts the legacy technical-momentum score**, a
separate lens, a documented coexistence). **Validated result (current fit):** composite
**OOS IC ≈ +0.037** (5 of 13 walk-forward folds negative — the edge is thin +
regime-dependent); top quintile ≈ **+1.35% / 4 wk at 52% beat-SPY**, bottom ≈
**−0.80% / 43%**; signed weights low_vol **−0.34** (reclaimed with a NEGATIVE weight —
high-vol outperformed in this large-cap bull period), mom_12_1 **+0.21**, mom_6_1
**+0.17**, trend_quality **+0.12**, rs_sector **+0.08**, turnover **+0.07**. **Honest
caveats:** survivorship + non-stationarity; the edge leans on low_vol's inverted sign
reflecting this 5-yr bull-ish regime; **re-run `fit_swing_model.py` periodically**;
regime-conditional weighting (Option C) is the planned next step. See the **"Validated
swing evaluation (Trade page) — DONE (2026-06-28)"** section below + the manuals
(rebuilt). Design/plan:
[design](docs/plans/2026-06-22-swing-validated-evaluation-design.md) /
[plan](docs/plans/2026-06-22-swing-validated-evaluation.md). **Prior — 2026-06-27**
(**Gamma Analyze → live Claude API + infographic +
4×/day auto-run**: the `/options/gamma` **Analyze** button no longer copies a prompt
to a dialog — it now **calls Claude (Sonnet 5, thinking disabled, ~1.5k max-tokens)
via a forced `submit_analysis` tool-use call** and renders the structured reply as a
self-contained dark **infographic** served in a new browser tab (mirrors Explain's
`/options/<view>` raw-`HTMLResponse` route pattern): a **regime banner + bias meter**,
a **per-index card** ($SPX/SPY/QQQ) with a **price-level ladder** (spot vs gamma flip /
call+put walls / expected-move band, with label de-collision) + **metric tiles** +
note + a **per-symbol what-if** (▲ rally / ▼ sell-off / ▬ chop), and a **"Why is this
happening"** section at the bottom. The **Exp. move** tile is a **code-authoritative
1-day EM** (`spot·ATM_IV·√(1/365)` via `compute._session_expected_move`) that overrides
the model's copy — the engine's `calc_expected_move_from_chain` is a 0-DTE
remaining-hours EM that collapses to ~0 off-hours / at the close (the bug that surfaced
SPX EM ≈ 3). It also **auto-runs on a schedule** (`scheduler.analyze_slot_due`: premarket
09:00 ET / ~18 min after open 09:48 ET / midday 12:30 ET / close 15:58 ET, once per
trading day within a 20-min grace) → `handlers.run_scheduled_gamma_analyze` caches each
under its **own slot key** (`cache:options:gamma_analyze_{premarket,open,midday,close}`,
separate from the ad-hoc `cache:options:gamma_analyze` so a scheduled run never
auto-opens a tab); the Gamma page's **Auto briefings** buttons open each via
`/options/analyze?slot=…`. Every failure degrades to a readable HTML page (no chains /
no key / API error / no tool reply); output carries **no disclaimers**. Anthropic key
resolved locally in `compute` (env `ANTHROPIC_API_KEY` → gitignored
`shared/anthropic_key.txt`; options_svc does NOT import driver_svc). See the **"Gamma
Analyze — Claude infographic + auto-run (DONE 2026-06-27)"** section below. **Earlier
this session — EoD report redesign + Scanner/Paper/Driver UX batch**:
the **`/eod` report** was rebuilt around **Daily / Weekly(WTD) / MTD performance per
book** (manual ledger + Driver, separately) + **trade-type breakdowns** (strategy /
0-DTE-Swing / status) + **TOC + collapsible `<details>` nav** (no JS — works in-app and
in the exported files); needs the new additive `compute.driver_account_view()`
**`closed_positions`** field. See the "EOD Report redesign — DONE (2026-06-27)" section
below + [design](docs/plans/2026-06-27-eod-report-redesign-design.md) /
[plan](docs/plans/2026-06-27-eod-report-redesign-plan.md). **Also shipped this session**
(Scanner / Paper Trades / Driver UX batch + denser nav, commit `36bcf40`): Scanner
Calculator-transfer fix (legs were wiped pre-chain-load — stash `pending_legs` +
`load_symbol`) + tab counts/colors + in-app new-signal toast + 3D Run-scan button;
Paper Trades **live unrealized P&L** (reprice open ledger trades via `signal_repricer`,
market-hours gated) + colored/decimal P&L + renamed headers + newest-first sort +
**red Delete buttons** (needed `color=None` so `.pt-danger` beats `bg-primary`) +
descriptive **Analyze popup** + speedometer PoP-fallback + "Current price" label;
Driver **today-only decision log** + colored perf P&L + Bucket/Instrument labels +
sticky headers + the **root-cause fix that the driver never opened a position**
(`open_driver_position` KeyError'd on `'signal_id'` because the driver feeds RAW scanner
signals `type`/`credit`/`id`, not `strategy`/`entry_credit`/`signal_id` — see
[[driver-feeds-raw-scanner-signal-shape]]); nav inter-item spacing halved + all groups
expanded by default. Prior — 2026-06-25 (**Driver isolated paper account + performance
scorecard**: the autonomous Driver now trades into — and measures itself against —
its **own dedicated paper book** (`options-scanner/data/paper_account_driver.db`, new
`repo_paths.DRIVER_PAPER_DB`, $25k start), fully isolated from the user's manual paper
account. This fixes a latent **write/read split** found while investigating "where do
the driver's trades show up?": the Driver **wrote** `paper_create` into System A (the
flat LEDGER `trades.db` — no repricing/auto-manage/account, so its trades were inert
rows and the `source="driver"` tag was silently dropped) but **read** its day-P&L /
$500-target / halt from System B (the user's `paper_account.db` ENGINE account) — so it
measured the **wrong book** and its trades never repriced. Now a new
**`driver_paper_create`** command (`services/options_svc`) opens each guardrail
survivor into `DRIVER_PAPER_DB` via the new `compute.open_driver_position(signal, qty)`
(extracted from `paper_engine.run_entry_cycle`'s per-signal block — simulated fill →
re-size on the ACTUAL fill credit → reserve BP → `insert_position`; the guardrail qty
is a **CEILING**, `min(clamped, sized-on-fill)`; never raises), and the **5-min manage
tick** reprices + auto-exits the driver account on the existing `manage_due` slot in its
**OWN guarded branch** (`compute.run_driver_manage_cycle`) so a driver failure can't
skip the manual refresh. options_svc publishes two new views:
**`cache:options:driver_paper_account`** (snapshot + open positions — **NO rescue
overlay**, that reads the manual book) and **`cache:options:driver_paper_perf`** (a PURE
`driver_perf.build_scorecard`: # trades, open/closed, **win rate**, **profit factor**
[None when no losses yet], avg win/loss, realized/unrealized/total P&L, best/worst,
**P&L by symbol & by strategy**). `driver_svc` rewired: `run_autonomous_cycle` enqueues
`driver_paper_create` (not `paper_create`), reads day-P&L + positions from the **DRIVER**
account (`CACHE_OPT_DRIVER_PAPER`), and attaches the scorecard to the published
**`AutonomousState.perf`** (new additive field); `build_packet`'s open-position
attribution is correct-by-construction (the whole driver DB is the driver's; the dead
`source=="driver"` filter falls back to the full account). The `/driver` **monitor
re-points** its Day-P&L bar / summary / open positions to
`cache:options:driver_paper_account` (was the manual `paper_account`) and gains a
**Performance scorecard card** (headline + quality chips, best/worst, by-symbol /
by-strategy tables) reading `cache:options:driver_paper_perf` (live — refreshes on the
5-min tick, not just the 30-min cycle). **driver_svc must NOT import `paper_engine`**
(it transitively pulls `scoring`/`signal_repricer` → the documented cross-app module
collision) — all engine calls stay in options_svc; driver_svc only enqueues + reads
cache. **Also this session** (supporting fixes, shipped): a **`DRIVER_MODEL`** override
(env → gitignored `shared/driver_model.txt` → default `claude-opus-4-8`) so the decider
runs e.g. `claude-sonnet-5` per-deployment; decision-log timestamps in **CST**
(`to_central`); the Enable/Disable toggle hardened (optimistic state + timeout warning —
the real "switch keeps turning off" cause was driver_svc being DOWN, i.e. no consumer
for the enable command). New: `DRIVER_PAPER_DB`; `open_driver_position` /
`run_driver_manage_cycle` / `driver_account_view` / `driver_account_perf` /
`driver_perf.build_scorecard` (options_svc); `driver_paper_create` / `driver_paper_manage`
/ `driver_paper_reset` commands + the two cache views; `AutonomousState.perf`. **PAPER
ONLY** — `config.PAPER_TRADE` stays True; the driver never flips it. options_svc **285**
+ driver_svc **138** + contracts **35** + webgui **510** green (incl. a Redis-driven e2e
proving a `driver_paper_create` lands ONLY in the driver DB — manual account untouched —
and both views + the scorecard reflect it). Built subagent-by-subagent (TDD, two-stage
spec+quality review per unit). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-25-driver-isolated-paper-account-design.md) /
[plan](docs/plans/2026-06-25-driver-isolated-paper-account-plan.md).)
Prior — 2026-06-24 (**Autonomous Driver — strategy-agnostic Claude
decision layer (level B, paper)**: the `/driver` morning agent's hardcoded
`trade_selector` rule tree (three fixed buckets — the reason only equity trades
ever appeared: the SPX-options + MES-futures branches hit hardcoded gates and the
tree fell through to the one branch that always passed) is **replaced by a
Claude-driven decision layer** that auto-selects + sizes **defined-risk option
credit spreads (PCS/CCS/IC) from the scanner menu** (`cache:options:scan`),
targeting **net $500/day**, in **autonomous paper** mode (NO human approval gate;
`config.PAPER_TRADE` stays True — this service never flips it). Pipeline (all new
in `services/driver_svc`): **`compute.build_packet`** projects the top-N
composite-scored scanner signals into a compact model-facing menu + day-P&L
gap-to-target, keeping a `menu_by_id`→RAW-signal map (real scanner field names:
structure in **`type`**, **`expiration`**, **`pop_pct`** — the plan's guesses were
wrong, caught during the build) → **`decider.decide`** (Claude **Opus 4.8** via the
new `anthropic` dep; a forced `submit_decision` tool-use call; ANY failure — no key
/ API error / malformed output — degrades to **stand-down**, never trades blind;
`import anthropic` is lazy) → **`guardrails.apply_guardrails`** (the **PURE
code-authoritative safety core**: a defined-risk allowlist, a per-trade + daily-
budget **quantity clamp**, and a **halt** at banked-$500 / daily-loss-cap / VIX>25
— the model PROPOSES, code DECIDES; the model never sizes its own risk; hardened
vs NaN/inf) → enqueue the EXISTING `cmd:options` **`paper_create`** per survivor (a
`source="driver"` COPY of the signal + the CLAMPED qty; the enqueue loop is
isolated so a mid-loop failure can't skip the halt-latch/publish). A
**`cache:driver:control`** key is the **master switch + STOP kill-switch** (default
**OFF** — the user explicitly enables); the scheduler fires a cycle at **09:28 +
every 30 min during RTH** (`checkpoint_due`) with a **next-day halt re-arm**
(`should_rearm`), each on the executor + per-branch guarded. The `/driver` page
flips to a **monitor + override** — Enable/Disable, a confirm-gated **STOP**, **Run
now**, a day-P&L-vs-$500 progress bar, open-driver-positions, and a newest-first
**decision-log** audit — reading **`cache:driver:autonomous`** (`AutonomousState`);
the **legacy approval queue + Performance** UI is preserved (gated off while
autonomy is enabled). The decider only PICKS from the scored scanner menu (never
invents strikes), and the legacy `trade_selector` is retained as a degrade path.
**Real `/ES` `/MES` futures options (FOP) were investigated and shelved** — Schwab's
API serves no FOP chains and places only EQUITY/OPTION orders (see
[[schwab-api-instrument-limits]] / the design doc); SPX/NDX index options are the
cash-settled 1256 equivalent if revisited. New contracts `DriverControl` /
`AutonomousState`; new `anthropic` dep + `ANTHROPIC_API_KEY` (env / gitignored
`shared/anthropic_key.txt`). driver_svc **130** + shared/contracts **34** + webgui
**483** green (incl. a Redis-driven e2e proving the model's requested qty=3 **clamps
to 1** through the REAL pipeline, and a banked-$600 cycle latches the kill-switch).
Built subagent-by-subagent (TDD, two-stage spec+quality review per unit). Branch
`Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-driver-autonomous-claude-decider-design.md) /
[plan](docs/plans/2026-06-24-driver-autonomous-claude-decider-plan.md).)
Prior — 2026-06-24 (**Calculator/Simulator UX batch — symbol tab/Enter
Load, wait overlay, Expiry→all legs, compact leg cells**: four Tier-1 UI changes to
`/options/calculator` + `/options/simulator` (no service/contract changes).
**(1)** The **Symbol** field now fires **Load** (Calculator) / **Fetch snapshot**
(Simulator) on **tab-out (`focusout`) + Enter (`keydown.enter`)** — `focusout` not
`blur` (NiceGUI binds the q-input ROOT where `blur` doesn't bubble) — deduped via the
new PURE `inputs.should_load(current, last_loaded)` so an unchanged symbol doesn't
re-fetch; the **Load/Fetch BUTTON still force-reloads** (bypasses the dedup), and a
`state["loading"]` re-entrancy guard collapses the focusout-then-button-click double
fire. **(2)** A **centered full-screen wait overlay** — new shared
**`pages/options/overlay.py`** `build_loading_overlay()` → a handle with
`.show(msg)`/`.hide()` (a `position:fixed` dimmed backdrop + `ui.spinner`, built once
per render) — shows on **user-initiated** loads (`show_wait=True`), hides on
chain/meta arrival (`_apply_chain`/`_apply_meta`), with a **safety timeout**
(`overlay.LOAD_TIMEOUT_SEC=30s`, shared) that also resets the dedup. The timeout was
**raised 15s→30s after live-measuring the Simulator's `sim_fetch` at ~19s for SPY**
(6870 contracts) — 15s fired before a real snapshot landed, hiding the spinner
prematurely; the overlay's PRIMARY dismissal is data-arrival, so the backstop must
exceed the slowest legitimate fetch. Mount-time auto-loads (persisted-state restore /
cross-page handoff) pass `show_wait=False` (no overlay flash on every navigation).
**(3)** The Calculator's **top-level Expiry propagates to ALL legs** (literal, incl.
calendars — the user's choice) via `leg_editor.apply_expiry` / PURE
`set_legs_expiry(legs, expiry)`, which re-syncs each leg's strike select to the new
expiry; wired on `expiry_sel.on_value_change` → `_on_expiry_change`, **guarded by
`state["applying"]`** so the programmatic expiry sets in `_apply_chain`/`_prefill`
don't fire it, and the editor **`dirty` flag is preserved** so an untouched
single-expiry template still routes through the analytic summary. The **Simulator has
no global expiry** (per-leg only), so this is Calculator-only. **(4)** The shared
**`leg_editor`** leg-table cells are **compact** (a `leg-row` class on each row +
`theme.py` `.leg-row` CSS: `min-height:32px` + trimmed top/bottom AND side padding)
and the **Type** column widened (`w-20`→`w-24`) so **`call`/`put` no longer clip**
(verified: "call" renders 20px in a 58px cell), and the **"Actions" header is
dropped** (an empty `w-10` spacer keeps the trashcan column aligned) — **both pages**
(shared editor). New PURE helpers (`should_load`, `set_legs_expiry`) + the overlay
handle are unit-tested; webgui **460 green**; **verified live** (Calculator: AAPL
tab-out + MSFT Enter load with overlay show/dismiss; Simulator: SPY tab-out → overlay
→ ~19s snapshot → legs populate at 732/731 near spot 733.24, status "SPY spot 733.24 ·
6870 contracts"; compact cells + full "call"/"put" + no "Actions" header on both).
Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-calculator-simulator-ux-changes-design.md) /
[plan](docs/plans/2026-06-24-calculator-simulator-ux-changes-plan.md).)
Prior — 2026-06-24 (**Gamma page overhaul — persistence, fixed strike
window, blended heatmaps**: a batch of `/options/gamma` fixes. **(1) Symbol no
longer reverts to `$SPX` on refresh** — the page reads one shared
`cache:options:gamma`, so the **dropdown syncs to the cached snapshot's symbol on
build**, a repaint **ignores any snapshot whose symbol ≠ the selected one** (so the
service's one-shot `$SPX` startup publish can't clobber it), and **selecting a symbol
auto-refreshes** it (`gamma._set_symbol`/`_on_symbol_change` + the `_maybe_repaint`
guard). **(2) Fixed ±N strike window** (`gamma.strikes_around`, N_SIDE=20) for the
bars **and** heatmap instead of a ±% band, so the candle/cell count — hence size —
stays consistent through the day; the heatmap `rowsize` is the **median** visible-
strike gap (not the min) so mixed-spacing names (QCOM/SPCX: 1.0 strikes among 2.5)
tile densely like `$SPX` (`_strike_step`). **(3) Heatmap cropped to the visible
near-spot window** before building cells — Charm/DEX/Vanna are non-zero across the
whole chain, so this cut ~45k→~2.4k points (~19×). **(4) Off-hours persistence** —
the candles + heatmap stay on the **last session's** data until the **next trading
day's midnight CT**, then clear (Fri persists through the weekend / holidays until
the pre-session midnight): `scheduler.active_session_date()`/`gamma_cleared()` +
**[SUPERSEDED 2026-07-11 — the overnight clear was REMOVED so the charts show pre/post-market; `gamma_cleared` is gone]** +
`gex_history_db.load_date_with_grid(date)`; `compute.gamma_snapshot` returns empty
in the overnight cleared window and loads the **active session date** for the
heatmap (service-side; the DB retains prior rows). **(5) Blended heatmaps** — both
the intraday **and Term** heatmaps render as a smooth **interpolated** image (no
cell borders / separator mesh), a **dark diverging colorscale** (`HEAT_STOPS`: net
≈ 0 fades to **transparent** so the dark page shows through, like the candlestick
chart; strong −/+ glow red/green), a **transparent** chart background, an
**off-white** (`#f5f5f5`) spot line, **no fade** on hover (`states.inactive`/`hover`
disabled), and a **press-and-hold tooltip** — a `chart.events.load` hook
(`_HEAT_PRESS_TOOLTIP_JS`) gates Highcharts' `tooltip.refresh` so the popup shows
**only while the left button is held** (mousedown → show + follow the cursor;
mouseup → hide); plain hover shows nothing. **(6) Term view bugfixes** — re-floats
JSON-stringified strike keys + widens the chain fetch to the **next 5 expirations
regardless of cadence** (`compute._term_chain`/`_count_expirations`, so weekly/
monthly-only names show 5 columns, not 1). **(7)** an off-hours `spot=None` snapshot
no longer 500s the page. webgui 455 + options_svc 256 green; verified live. Branch
`Using_Highcharts`.)
Prior — 2026-06-24 (**Calc "Number of strikes" + Calc/Sim state persistence**:
two changes. **(1)** The Calculator's **Range min/max/%** controls are replaced by a single
**Number of strikes** input (default 24): the P&L grid now draws **±N real chain strikes
around spot** (strictly — a far-OTM leg can fall off; raise N to see it). New pure
`calculator.strikes_window(strikes, spot, n)` (the n strikes ≤spot + n >spot from the
front-expiry call∪put ladder) feeds an explicit **`price_rows`** list into
`compute.calc_compute` → engine `calc_spread_pnl(price_rows=…)` (additive — used verbatim
as the grid rows, else the even-step ±N heuristic fallback). `calc_compute`'s
`range_min/max/pct` params + `symmetric_price_range` are **removed**; the `calc_compute`
handler is `**args`-generic so it needed no change. **(2)** Both `/options/calculator` and
`/options/simulator` now **persist full UI state across navigation** and **auto-refresh on
return** — a single-user module-level snapshot (`_LAST_CALC`/`_LAST_SIM`) captures every
input (symbol/strategy/legs/fields/sliders[/active tab]) on change and restores it on
`render()` under a `restoring` guard (so wiring fires no stray commands); restored legs
ride each page's existing `pending_legs` hook so the post-fetch re-run uses them (an
explicit **Copy-to-Calculator/Simulator handoff still wins** over the snapshot — see
`page_state.pick_seed`). Survives navigation + browser reload; resets on a webgui restart
(like every persisting page). New PURE `webgui/pages/options/page_state.py`
(`snapshot`/`merge_restore`/`pick_seed`). options-scanner 17 + options_svc 249 + webgui
450 green; verified live (Number-of-strikes: AAPL grid = 24 rows = ±12 real strikes
265→322.5; persistence: AAPL+12 / MSFT restored across nav + price auto-refreshed;
service contract via Redis: N=5 → exact ±5 strikes). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-calculator-simulator-state-persistence-design.md) /
[plan](docs/plans/2026-06-24-calculator-simulator-state-persistence.md).)
Prior — 2026-06-24 (**Simulator What-if P/L fix**: the `/options/simulator`
**What-if** payoff had two bugs vs the Calculator for the *same* trade. (1) **Missing
×100 contract multiplier** — the simulator engine prices in **per-share × qty** units,
so a 10-lot spread's curve read **100× too small** (a real ~$14.5k max loss showed as
**−$200**). `compute.sim_run` now scales each `whatif_rows` `theo_price` by
`_CONTRACT_MULT=100` → a **dollar** position value (the Calculator scales by the same
literal 100). (2) **Wrong P/L baseline** — `whatif_pnl` subtracted the position's value
at spot at the **forward** time ("zero at spot"), so the profit side capped at **0** and
the whole curve was off by the credit. It now measures **from entry**: `sim_run` returns
**`whatif_baseline`** = the position's $ value at **spot, NOW** (the entry mark, Δt=0
full DTE), and `whatif_pnl(df, spot, baseline)` / `whatif_figure(..., baseline)` plot
`value(S,t) − baseline` — identical to the Calculator's `entry_credit + value(S,t)`
([options_calculator.py](options-scanner/options_calculator.py) `val += price*q*100;
pnl=entry_credit+val`), so **profit caps at the net credit, loss floors at width−credit**,
and theta now shows as the **Δt** slider moves (the old framing pinned spot to 0,
hiding it). No-baseline `whatif_pnl` keeps the legacy nearest-spot fallback (back-compat
/ pre-restart cached results); the IV-shock + Replay tabs are unchanged. Verified on the
**real** engine (not just the fakes): a 20-wide 10-lot SNDK call credit spread yields
`|max-profit| + |max-loss| = $20,000` (= width×100×qty) with profit=credit and
loss=−(20000−credit). webgui 438 + options_svc 253 green. **Restart `options_svc` +
reload the page** to see it live (the running service/page are stale). Branch
`Using_Highcharts`.)
Prior — 2026-06-24 (**Trade Analyzer theme + Markov near-term fix**: the
`/trade` page now wears the shared dark-navy **"dashboard" theme** (`ui.add_css(
DASHBOARD_CSS)` + `.calc-v2` wrap from `webgui/pages/options/theme.py`; header +
verdict + secondary cards are `calc-card`s, the Analyze button is `cv2-btn-primary`).
**Dead space removed**: the verdict row switched `items-stretch` → **`items-start`**
so the short Position/Investor cards size to content instead of stretching to the tall
Markov card (verified live: 308/276px vs the Markov card's 453px — was ~150-180px of
empty bottom each). **Markov chart fix** for "looks the same for every symbol": the
5/10/20d forecast **converges to the bull-leaning pooled-prior stationary within
~10 days** (only the near term is score-specific), so `trade_svc.compute.build_markov_block`
now emits an **additive** dense **`trajectory`** (`_MK_TRAJECTORY_HORIZONS=[1,2,3,5,10,20]`,
reusing the tested `forecast()`) and `trade.markov_forecast_figure` plots it
(`now→1d→2d→3d→5d→10d→20d`, falling back to `horizons` for back-compat) — the
score-specific early path is now visible (verified live: XOM Strong-Bear opens
red-dominated, INTC Strong-Bull green-dominated, NVDA between). The chart is
dark-themed (transparent bg, light axes, fixed `{value}%` y-axis). **`horizons`
(5/10/20d cards), `drift`, `tilt`, `markov_adjusted_score` are unchanged** — the
trajectory is chart-only, the verdict label/score math untouched. **Tab-out =
Analyze**: a **`focusout`** handler (NOT `blur` — NiceGUI binds to the q-input ROOT
and `blur` doesn't bubble there, same reason `select_all_on_focus` uses `focusin`)
fires Analyze, deduped via `should_request` (collapses the blur-then-click double
fire). **Last analyzed symbol persists** across navigation: the input seeds from the
cached `trade:analysis` result's `symbol`. webgui 435 + trade_svc 41 green; verified
live end-to-end (themed render, dense chart, tab-out analyze, symbol persistence).
Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-trade-analyzer-theme-layout-markov-design.md) /
[plan](docs/plans/2026-06-24-trade-analyzer-theme-layout-markov-plan.md).)
Prior — 2026-06-24 (**App theme rollout + What-if payoff**: the dark-navy
**"dashboard" theme** is now **shared, not Calculator-only** — extracted to
**`webgui/pages/options/theme.py`** (`DASHBOARD_CSS`, scoped under `.calc-v2`) and
injected by **both** the Calculator **and** the Simulator (`ui.add_css(DASHBOARD_CSS)`
+ wrap in `.calc-v2`), so the look never drifts. The **Simulator** gains the navy
gradient, bordered `calc-card`s, the **boxed** Strategy picker, **header-table** legs,
`cv2-btn`/`cv2-btn-primary` buttons, and **dark transparent Quasar tabs** so its
already-dark-transparent Highcharts panels sit on the navy. See the new **"App theme
— dark-navy 'dashboard'"** reference section below (palette + class vocabulary +
how-to-apply) — the single place to look up the theme. The Simulator **What-if** tab
is restyled as a **green/red profit-loss payoff**: `simulator.whatif_figure` plots
`P/L = theo_price(S) − theo_price(spot)` (`whatif_pnl`, **zero at spot**) as a
Highcharts **area** with `threshold:0` + `color`/`negativeColor` + `fillColor`/
`negativeFillColor` (green profit fill+line above the breakeven, red loss below; an
explicit base `color` stops a default-blue base path leaking under the split) + faint
full-height Profit/Loss `plotBands` with labels; `theo_price` is already
sign-weighted by leg side (`aggregate_position` scale `sign*ratio`), so the
subtraction is the holder's P/L and the direction is correct (verified live: a
24-DTE SPY bull put spread loses on the downside, profits on the upside). webgui 430
green. Branch `Using_Highcharts`.)
Prior — 2026-06-23 (**Multi-leg Simulator + Calculator DONE**: the
`/options/simulator` and `/options/calculator` pages now build, price, and analyze
**multi-leg strategies** — verticals (credit *and* debit), condors (iron +
all-same), butterflies (long 1-2-1 + iron), and **calendars/diagonals** (per-leg
expiry) — with **editable legs** and a **copy-legs button both ways**
(Simulator ↔ Calculator); existing singles + PCS/CCS/IC stay. New shared **pure**
`webgui/pages/options/strategies.py` (normalized leg dict + `STRATEGY_TEMPLATES`/
`STRATEGY_GROUPS` + `build_default_legs` + analytic-vs-numeric `summary_code`) and
`webgui/pages/options/leg_editor.py` (one parameterized editable leg-table widget
both pages mount — `state['legs']` is the source of truth so re-renders never lose
edits; each page injects its own `strikes_for`/`expiries_for` + `show_premium`).
Engine `options_simulator/engine.py` gains `Leg.ratio` (+ `Position.from_legs`) so
`aggregate_position` scales each leg by `sign*ratio` (butterfly body = 2×). Calc
engine: `calc_spread_pnl(per_leg_expiry=True)` prices each leg at its own
time-to-expiry (calendars) + new `calc_summary_generic` (numeric max-P/L /
breakevens / PoP off the value-at-front-expiry curve) for butterfly/condor/
calendar/`CUSTOM`; `compute.calc_compute` routes analytic (PCS/CCS/IC/singles) vs
generic and runs the grid to the **front (nearest) leg expiry**. `compute.sim_run`
(per-leg **elapsed** What-if Δt — a deliberate change from absolute-DTE, fixes
calendars) + `compute.sim_replay` are now **multi-leg** (back-compat with the old
single-contract args); `handlers` forward a `legs` arg; `handoff.py` adds the
`simulator`/`calculator_legs` stashes + `send_to_simulator`/`send_to_calculator_legs`.
options-scanner engine+calc + options_svc 252 + webgui 419 green; verified live
(SPY calendar `sim_run`/`sim_replay` 234-bar trace + an iron butterfly with exact
`max_loss=2300`/`breakevens=[727,741]`). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-23-simulator-calculator-multileg-strategies-design.md) /
[plan](docs/plans/2026-06-23-simulator-calculator-multileg-strategies-plan.md).)
Prior — 2026-06-22 (**Markov 2.0 (Trade Analyzer) DONE**: the `/trade`
**Position** verdict gains a probabilistic forward layer — the composite score is
discretized into **5 bands** (edges = the ±40 BUY/SELL cuts + ±15 neutral); a
per-symbol day-to-day transition matrix is **Bayesian-shrunk** toward a pooled
(17-symbol) prior; `P^n` projects **5/10/20-day** band distributions →
P(BUY)/P(SELL)/E[score]. New PURE engine `trade-analyzer/src/analysis/markov.py`
(classify/count/shrink/project/forecast/`drift_tilt`); `trade_svc.compute`
`reconstruct_daily_composite` (a daily-only **"Markov base score"** so history is
reconstructable) + `build_pooled_prior`/`get_prior` (cached
`cache:trade:markov_prior`, lazy daily) + `build_markov_block` wired **defensively**
into `analyze()`; an additive optional `markov` block on the `TradeAnalysis`
contract; a **Markov Forecast card** — the third **equal-width frame in the verdict row**
alongside Position/Investor — (stacked-area band-probability chart + per-horizon
metrics + a bounded **±12pt confidence-weighted drift tilt** surfaced as a
`markov_adjusted_score` Position headline — **verdict label unchanged**). No
feedback by construction (chain on `composite_daily`, tilt on `composite_full`).
trade-analyzer 215 + trade_svc 40 + contracts 26 + webgui 385 green; verified live
(AAPL). Branch `Using_Highcharts`. See "Markov 2.0 (Trade page)" below.)
Prior — 2026-06-21 (**Rescue Tested Trades DONE**: new `/options/rescue`
page + an advisory/one-click-apply rescue feature for tested credit spreads (PCS/CCS/
IC). Hybrid arch ("Approach C"): cheap at-risk detection rides the existing 5-min
manage cycle (tags paper-account rows with `rescue_state`/`heat` + publishes
`cache:options:rescue_summary` for a nav badge); the ranked candidate menu is computed
on-demand via a `rescue` command → `cache:options:rescue:<position_id>`; apply executes
via new paper-engine primitives behind a stale-price guard. New PURE engine
`services/options_svc/rescue.py` (11 candidate builders + risk/context/scoring),
`compute.compute_rescue`, `handlers.rescue`/`rescue_apply` + summary overlay,
`options-scanner/paper_adjust.py` (apply primitives + dispatcher), `paper_account_db`
`position_adjustments` table + `parent_position_id` col, `config/commissions.toml`
(commission source of truth), `RescueAdvisory`/`RescueCandidate` contracts.
shared/contracts 24 + options_svc 226 + webgui 372 green; options-scanner 1056 (12
pre-existing fails). Verified live (real INTC paper positions). Branch
`Using_Highcharts`. See "Rescue tested trades" below.)
Prior — 2026-06-20 (**Replay + Expected Move look-back, DTE-aware**: the
Simulator Replay path and the Expected Move trailing history now size to the
selected contract's **DTE** (Replay tiers 1-min/1d → daily/~½×DTE; EM ≈ **3× DTE**)
with an **auto + manual-override** look-back dropdown on each tab. EM also
**collapses non-trading-day gaps** — renders via `ui.highchart(type="stockChart")`
→ ordinal x-axis + a trading-day-only `em_cone` (reuses `scheduler._HOLIDAYS`) — and
the Replay hover tooltip is capped at 2dp. 356 webgui + 145 options_svc tests green;
verified live. Branch `Using_Highcharts`. See "Simulator Replay tab" + "Expected
Move page".)
Prior — 2026-06-20 (**Simulator Replay tab migrated**: the third legacy
Tk simulator tab (`Replay`) now lives in the 3-tier webgui alongside What-if /
IV-shock — new `compute.sim_replay` + `sim_replay` command +
`cache:options:sim_replay` + a stacked price+5-Greek Highcharts panel with a
client-side scrub cursor. Verified live (SPY 62-bar trace). See "Simulator Replay
tab" below.)
Prior — 2026-06-19 (**charting migrated Plotly/SVG → Highcharts**: every
webgui chart + gauge now renders via `nicegui-highcharts` (`ui.highchart`), not
`ui.plotly`/inline-SVG. Pure builders return Highcharts option dicts; in-place updates
are `el.options = fig; el.update()` (replaces `update_figure`). The Sentiment / Trend /
Trade-detail speedometers are the shared **`webgui/pages/gauge.py`** angular gauge
(painted red→yellow→green rainbow face + needle). Key gotchas now in the "NiceGUI
gotchas" section: the `gauge` type needs NO `extras` (auto-loads via `loadMore`;
`extras=["highcharts-more"]` throws), `solid-gauge`/`heatmap` ARE valid extras, a
dynamically-added chart needs a chart already present at first render (ESM import map),
`bar` axis is reversed by default, and `chart.update()` leaks config across a
series-type switch (recreate on kind-change). `plotly` was never a Python dependency.
322 webgui tests green; verified live. Branch `Using_Highcharts`.
Prior — 2026-06-19 (**intraday Market Trend redesign**: the Sentiment tab's
Market Trend gauge is now a responsive **directional 0–100 score** recomputed every
15 min — Price/MTF 45% + Breadth 25% + Sector 20% + VIX 10%, confidence-weighted,
EMA-smoothed needle, 5-state mapped (range widened to 30–70) onto the bridge with
2-read hysteresis so `regime_filter` is unchanged. New pure
`sentiment-dashboard/scoring/intraday_trend.py` + `services/sentiment_svc/compute.py`
`compute_intraday_trend`/`compute_30d_trend` + 15-min gated/persisted refresh +
additive bridge fields (`trend_score`/`sub_scores`, daily `sma_*` kept). Second gauge
is now the **30-Day structural** trend. See "Intraday Market Trend model" below.
Prior — 2026-06-19 (**perf-fix batch 2**: implemented the remaining High +
all Medium audit items — webgui health-cache + off-thread/de-duped alert watcher +
in-memory `app_settings` cache, Gamma's four polls coalesced into one cheap pipelined
`read_versions`, `Bus` cheap `:ver` version reads + `consume_commands` group-create
once, `technical` EMA/MACD/volume-profile vectorized, `sectors_ref` mtime-cache,
trader-path pooled session, sargable `gex_history_db` today-query. All suites green
(2 pre-existing sentiment-dashboard UI-import fails aside). Prior same-day: **perf-fix
batch 1** + **end-to-end audit pass**: corrected doc drift —
full `config/ports.toml` (memurai + ml_servers + services 8210–8214), the
`pages/options/handoff.py` + `pages/ui_guard.py` shared helpers, the Sentiment
3-column / 2×2-tile layout, the proxy `/pricehistory` 404-flood fix, and paper
auto-manage (no longer "manual-only"); added a **"Performance characteristics &
known hotspots"** section from an efficiency audit. Prior same-day: **System
Status page DONE**: new `/status` pure-webgui
page probes every tier — Memurai PING, schwab-proxy `/health`, the five domain
services' `/health` (:8210–8214), and webgui itself — into an overall up/down
banner + per-component cards, plus a **published-data-freshness** table (each
domain's latest cache version + age, flagging scheduled views gone stale). 304
webgui tests green. See "System Status page (`/status`) — DONE" below.
Prior — 2026-06-18 (**EOD Report page DONE**: new `/eod` + `/eod/detail`
pure-webgui pages aggregate the collected `options:*` + `driver:*` caches into a
**Summary** rollup + **Detailed** report, with a **Generate** button that snapshots
the caches into standalone `summary.html`/`detail.html` archived under
`webgui/data/eod/<date>/` (in-app view + dated archive + `/eod/file` raw serving).
See "EOD Report page (`/eod` + `/eod/detail`) — DONE" below.
Prior: **3-tier migration — all five domains migrated** (Sentiment, Options,
Portfolio, Trade, Driver) — every page reads Redis and the webgui imports only
`nicegui` + `shared.bus` + `shared.contracts`. Remaining: Phase 6 retire-shims
(`regime_filter` reads Redis; drop the bridge dual-write).)

---

## Feature notes migrated out of CLAUDE.md (2026-08-16)

These are the `— DONE (date)` feature narratives that had accumulated inside CLAUDE.md's
"webgui structure" and "webgui development notes" sections. They are shipping history —
what was built, the pieces, the test counts at the time, and the live-verification logs.
Moved here verbatim so CLAUDE.md keeps only durable architecture and conventions.
Design/plan links inside them are relative to the repo root, so prefix them with `../`.

**Sentiment (`/sentiment`) — DONE.** `webgui/pages/sentiment.py` reuses the
copied `history_backfill.backfill_history(...)` engine (latest completed-session
composite + 30d history) + `scoring` (`composite.velocity/divergence`,
`trend_regime.classify/commit_state`) + the ported `sectors_ref.load_sectors_data`.
**Layout:** a three-column top region — **Market Sentiment ring** / **Market Trend
ring** / **Signals** — each column an equal-width `min-w-[300px]`. **Since
2026-08-14 the four semicircular gauges are two concentric Day/Week/Month rings**
(see "Sentiment Day/Week/Month rings" below); the Sentiment ring keeps bias +
size/conf beneath it, the Trend ring keeps the regime badge/desc and the **TREND
DETAIL** press-and-hold popup showing the four sub-scores (Price/Breadth/Sector/VIX)
+ confidences (`trend_subscore_rows`). Trend values come from `derived["trend"]` /
`derived["trend_7d"]` / `derived["trend_30d_ago"]` **published by `sentiment_svc`**
via the **intraday Market Trend model** (see the dedicated section below). The
component **table** (Value/Score[2dp]/Weight/Conf — Contrib computed for
reconciliation but not shown; credit_pulse excluded per v4.3 `WEIGHTS`) and the
Trend detail are **press-and-hold popups** (`ui.menu().props("no-parent-event")`),
not always-visible columns. The Signals column is a **1×4 vertical stack of glowing
tiles** (`SIGNAL_TILE_DEFS` = **Bias/Signal/Yesterday/Change** — Modifier dropped per
design), each tile icon + letter-spaced label / big neon-shadowed value / hairline
rule + dot / footer icon + descriptor, tinted from a finite four-key `TONE_CLASSES`
map (pos/neg/warn/flat), with the service's **velocity + divergence lines** beneath
(`velocity_lines`). (A dollar-weighted call/put **premium**
skew tile lives on the **Market Dashboard** OPTIONS SENTIMENT frame, NOT here — see the
"Market Dashboard" section + the 2026-07-21 Last-updated entry.) Below that, an **expanded-by-default**
(since 2026-07-12) `ui.expansion("Daily Sentiment & Trend")` holds **two stacked value-colorized 2-min
intraday graphs** — Daily Market Sentiment (0–10) + Daily Market Trend (**shown 0–10**, stored
0–100 ×0.1), each a Highcharts line colorized green/yellow/red by value via
`series.zones`/`zoneAxis:"y"` (`build_sentiment_intraday_figure`/`build_trend_intraday_figure`;
sentiment bands ≤4.5/≤6.5, trend bands ≤3/≤7 on the 0–10 scale), over a **synthetic contiguous
index (category) x-axis** — trading days PACK together with no overnight/weekend dead space, a
null slot breaks the line between days, tick labels at day boundaries, CT date+time in each
point's tooltip `name` — rolling the **last 5 trading days**. Deliberately a PLAIN chart, NOT a
stockChart (whose in-place `chart.update()` throws and silently freezes an open page — see the
NiceGUI gotchas). The series is **recorded going forward** (no
backfill) by `sentiment_svc` — each 120 s `refresh()` records one `(ts, sentiment, trend)`
point **RTH-gated** (Mon–Fri 08:30–15:00 CT) into the SQLite store
`services/sentiment_svc/intraday_history_db.py` (`repo_paths.SENTIMENT_INTRADAY_DB` =
`sentiment-dashboard/data/sentiment_intraday.db`; rolling window = last 5 distinct local
dates; one shared connection serialized by `handlers._INTRADAY_LOCK` across the
multi-worker executor), then publishes `cache:sentiment:intraday_history`
(`{"points":[{ts,sentiment,trend},…]}`; additive `IntradayHistory` contract). The page
reads that view in `_read_cache` (it rides the composite version bump — published in the
same refresh cycle), paints both charts in `_apply`, and **reflows on expand** (a
`@guard`-wrapped worker — charts built inside a collapsed expander measure 0×0, the
documented Simulator-hidden-tab fix). This **replaced** the old 30-day composite-history
chart + 5d/20d rolling-average + velocity/divergence text lines. **NOTE (2026-07-12):** the
**Sector & Industry Performance** table below was **moved to its own `/sentiment/sectors` tab**
(`pages.sentiment_sectors`) — the description that follows now documents THAT page (the builders +
cache view are unchanged; only the containing route moved). The
full-width
**Sector & Industry Performance** table
(11 sectors × Day/Week/Month %, P/C, RRG; per-cell colored; subtle gridlines + row
hover via a `.sent-sectors` `ui.add_css` block) with a rotation banner
(`scoring.rotation.compute_rotation`) + "% green | Cap-wtd | Score" summary, and a
bottom **status bar** (Updated/Next/Sectors/Proxy — proxy checked off-thread in
`load()`, cached, not on the status timer). Each sector **expands** into its industry
sub-rows (▷ toggle or Expand/Collapse All; lazy-fetched via `_load_industries` +
cached; industries show Day/Week/Month % **and P/C + RRG**).
The sector load (`_load_sector_perf`, ~24 proxy calls incl. 11 `/chains` for P/C)
runs at startup + on manual Refresh **+ once per RTH hour in the service** (`sentiment_svc scheduler.sectors_due`, 2026-07-09 — the P/C is a live option-VOLUME ratio that is empty premarket, so a premarket stack start used to leave the P/C column blank all day). **Auto-refresh is server-side and
tab-independent:** a module-level background task (`start_background_refresh` →
`refresh_cache`, started from `main.py` `@app.on_startup`, mirroring
`scanner.start_autoscan`) updates `_CACHE` (composite-only) **and republishes the
bridge every 120 s** regardless of any open/active tab — even with no browser open.
The page **never fetches on activation**: it paints from `_CACHE` instantly and a
fetch-free `ui.timer(120, _repaint_from_cache)` tracks the background cache;
**manual Refresh** is the only page-driven fetch. Persists across navigation via
`_CACHE` (single-user), incl. expanded sectors. Verified against the live proxy to
match the source dashboard exactly (82% green | Cap-wtd +0.70% | Score 7.8/10; real
industry rows on expand). Designs/plans:
[base](docs/plans/2026-06-14-sentiment-page-design.md) /
[sector-perf](docs/plans/2026-06-14-sentiment-sector-perf-design.md) /
[persistence+industries](docs/plans/2026-06-14-sentiment-persistence-industries-design.md)
(+ matching `-plan.md` files).
**Intraday Market Trend model (DONE — 2026-06-19).** The Market Trend panel is
driven by a **directional 0–100 score** (50 = neutral, 100 = max bull) recomputed
**every 15 min**, replacing the old slow daily 5-state SPY classifier. Pieces:
- **Pure scoring** `sentiment-dashboard/scoring/intraday_trend.py` (scalar in/out, no
  I/O): four directional sub-scores — `score_price` (45%: MTF EMA alignment + VWAP +
  MACD/RSI, **ADX-scaled** so chop hugs 50), `score_breadth_dir` (25%: A/D + %>50DMA +
  H/L), `score_sector_participation` (20%: # green + cyclical-vs-defensive day%-spread),
  `score_vix_context` (10%: level/change/term) + `vol_confidence_factor` (a VIX-spike
  damper on aggregate confidence) — blended by `blend_trend` (confidence-weighted, same
  idiom as `composite.blend`). `score_to_state` maps the score → the existing 5-state
  vocabulary (**80 bull / 70 pullback / 30–70 range / 20 bear_rally / bear**); hysteresis
  reuses `trend_regime.commit_state` (2 reads to flip). `TrendSub` is a local float
  dataclass (NOT the int-only `ScoreResult`).
- **Service compute** `services/sentiment_svc/compute.py`: `compute_intraday_trend`
  (fetches SPY intraday 5/15-min + daily via the proxy's new
  `get_intraday_history`, breadth/sector/VIX quotes — reuses `live_composite._BREADTH/_last/
  _VIX_SYMS` and standalone `technical`) and **two structural horizons**,
  `compute_30d_trend` and — added 2026-08-14 for the Trend ring's Week arc —
  **`compute_7d_trend`**. Both structural functions are thin wrappers over the shared
  **`_structural_trend(spy_daily_df, sector_pcts, cyc_def_scale)`** (price + sector only,
  no VWAP/breadth/VIX, no smoothing or hysteresis) and differ ONLY in which horizon's
  sector %-moves they pass (`week_pct` vs `month_pct`) and in `cyc_def_scale`
  (`_CYC_DEF_SCALE_7D = 1.5` vs `_CYC_DEF_SCALE_30D = 3.0`). Each owns its own TTL cache
  on the self-fetching path (`TREND_7D_TTL_SEC = 1800` / `TREND_30D_TTL_SEC = 3600`;
  explicit-args calls bypass it). All three are defensive → neutral on any failure —
  **and that neutral is a trap, see the ring section below.**
  ⚠ **`compute_30d_trend` is MISNAMED** and always was: it is a monthly-HORIZON
  *structural* read, not the trend as it stood 30 days ago and not a 30-day average.
  ⚠ **KNOWN LIMITATION:** the Week and Month horizons **share the same daily price
  sub-score** — `technical.calculate_ema_alignment`'s EMA periods are fixed, so handing
  it a shorter frame changes nothing. The two arcs therefore track each other and diverge
  mainly on **sector rotation**. A genuinely weekly price read needs weekly-resampled SPY
  bars; deliberately deferred.
  **One sector fan-out serves both horizons** — `_fetch_sector_pcts` (TTL
  `SECTOR_PCTS_TTL_SEC = 3600`, an EMPTY result deliberately NOT cached so a proxy blip
  can't poison the hour) returns `{"week": …, "month": …}` off ONE `_fetch_closes` call,
  which already derived both, so **the Week arc costs ZERO extra Schwab calls** on a stack
  measured at ~68–76k/day. `_fetch_sector_week_pcts`/`_fetch_sector_month_pcts` are views
  on it.
- **15-min cadence + persisted state** in `handlers.refresh` via the module-level
  `_TREND` holder (lock-guarded; `scheduler.trend_due`/`TREND_INTERVAL_SEC=900`): the
  EMA-smoothing + hysteresis state thread across reads; the held trend rides inside the
  existing `cache:sentiment:composite` as `derived.trend` / **`derived.trend_7d`** /
  `derived.trend_30d_ago` (no new Redis key). The two structural horizons carry **no
  hysteresis of their own — they are simply HELD** in `_TREND` and republished on gated
  (non-recompute) refreshes, so every composite write carries all three ring arcs.
  `derive_composite_extras` takes `trend_7d` **last** so the existing positional call
  shape is unaffected.
- **Bridge** (`live_composite.build_bridge_payload` + `compute._bridge_trend`): the
  intraday `state`/`confidence` + additive `trend_score`/`sub_scores` are merged onto the
  daily `classify` `sma_*`/`drawdown` (kept for the additive-only contract). `regime_filter`
  reads `state`/`confidence` unchanged (state strings + vote map identical). The standalone
  `publish_bridge` GEX path stays on the daily classify.
- **Page** `webgui/pages/sentiment.py`: `trend_gauge_value` returns the score directly;
  `trend_subscore_rows` feeds the TREND DETAIL popup. Verified live end-to-end (compute →
  Redis → bridge → rendered gauges + popup). Design/plan:
  [design](docs/plans/2026-06-19-intraday-market-trend-redesign-design.md) /
  [plan](docs/plans/2026-06-19-intraday-market-trend-redesign-plan.md).

**Options Flow panels (`webgui/pages/options/flow_panels.py`) — 2026-08-15.** The
Gamma page's **Flow** and **Net Prem** subtabs are two dark-console panels —
**Premium Divergence** and **Flow Field** — built to a supplied spec in the same
visual system as the plasma heatmap. Design/plan:
[design](docs/plans/2026-08-15-options-flow-redesign-design.md) /
[plan](docs/plans/2026-08-15-options-flow-redesign-plan.md).
- **They are NOT Highcharts.** Hand-rolled SVG strings mounted with `ui.html`,
  updated via `el.content` — the `pages/rings.py` / `pages/regime_mix.py` idiom.
  Three reasons: the spec IS SVG (hairline strokes over a translucent halo, a
  per-segment two-tone ribbon, decluttered terminus labels); the repo already has
  the idiom twice; and it sidesteps both documented `ui.highchart` hazards at once
  — the ESM-import-map trap and the `chart.update()` merge leakage that made
  `gamma._set_chart` RECREATE the element on a Flow ↔ Net Prem switch.
- **The whole panel is ONE raw HTML fragment** (chart + chips + ladder + rail),
  the documented **out-of-scope** case for the Tailwind-first standard — the same
  exemption the Calculator's P&L heatmap and the Gamma Explain block use.
  Everything moves together under one cursor and the scrub is client-side, so
  building the chrome from NiceGUI components would mean JS reaching across into
  Quasar's DOM. The ONE thing that cannot be inlined — the pulse keyframes — is
  `theme.FLOW_KEYFRAMES_CSS`, this page's only `ui.add_css` addition.
- **⚠ The DOMPurify allowlist is the binding constraint, and it has already cost a
  real defect** (the sentiment rings' `dominant-baseline`). `ui.html` sanitizes
  through NiceGUI's BUNDLED DOMPurify, not the native sanitizer. So: `dy="0.35em"`
  for vertical centring, **no `foreignObject`** (the spec's own reference markup
  uses it — do not port that part), **no `<filter>`** (the glow is a layered halo,
  which the spec specifies anyway), and **no `data-*` attributes** — the
  allowlist extraction reads NAME LISTS out of the bundle and therefore cannot see
  `ALLOW_DATA_ATTR`, so a `data-*` attribute is unvouched-for even though it would
  in fact survive. `test_flow_panels.py` guards all four panel states.
- **The Flow Field's DOLLARS / SKEW % toggle is a REAL control inside the
  fragment.** DOMPurify strips inline `on*` handlers, so the click is bound by
  the same script channel as the scrub (`addEventListener`) and reaches Python
  through NiceGUI's global **`emitEvent` / `ui.on`** pair (`flow_panels.
  MODE_EVENT`, `toggle_js`). `toggle_js` is emitted on EVERY paint independently
  of the scrub payload — the toggle exists in the empty state too. The old
  `np_mode_sel` select is **kept but hidden** as the state HOLDER: every reader
  already goes through `np_mode_sel.value` and the persist+repaint path hangs off
  its `on_value_change`, so the toggle writes through it and one code path still
  owns the change. **The click payload is UNTRUSTED** (it is persisted to
  settings.json and read back on the next build) — `normalize_mode` guards it on
  both sides, and the handler unwraps the bare-string / one-element-list shapes
  `emitEvent` can produce. Labelled SKEW %, not the spec's "PERCENTILE", because
  that is what the mode computes (a signed share of session premium, bounded
  ±100%) and what the rest of the app calls it.
- **The scrub is fully client-side** (`scrub_js`, shipped via `ui.run_javascript`,
  so nothing there is sanitized). Coordinates are computed SERVER-side and shipped
  in the payload rather than re-derived in JS — two implementations of one mapping,
  and the first symptom of their drifting apart is a cursor dot sitting off its own
  line. The fragment is replaced wholesale per repaint, so listeners go with the
  old DOM and there is nothing to unbind; the bind is **deferred one tick** because
  `el.content` applies on the client asynchronously.
- **The strike ladder needs a stored per-strike premium history** — see the
  `"prem"` view note in the folder map. It is **forward-only**: empty until
  `options_svc` has restarted and collected, and the panel SAYS so rather than
  drawing a blank frame that reads as a real reading.
- Palette is the page-scoped **`[flow]`** section of `config/theme.toml`
  (`theme.flow_colors` → `FLOW_COLORS`), following `[console]`/`[macro]`.
  `call`/`put` are pinned to `gamma.POS_COLOR`/`NEG_COLOR` — the panels sit behind
  the same subtab strip as the heatmap, so a cyan meaning "call" on one tab and
  something else on the next would be worse than no colour coding at all.
  Per-symbol line colours stay in `gamma.NET_PREM_COLORS` (28 symbols, pinned for
  mutual distinctness); a 15-hex section could not cover them.
- **`flow_figure` and `net_prem_figure` are GONE.** The readers underneath them
  (`_np_selected` / `_np_rows` / `net_prem_value`) are unchanged and now feed the
  panel directly, as do `flow_summary_text` / `net_prem_summary_text` /
  `net_prem_status_text` (the last reports a stale publish INSIDE the collection
  window — a failure nothing else on the page can see, which is why it is
  clock-driven rather than repaint-driven). The `chart_kind` registry in
  `test_options_gamma.py` carries an explicit `_PANEL_VIEWS` set so its
  completeness guard still FAILS on a new unregistered view.

**Sentiment Day/Week/Month rings (`webgui/pages/rings.py`) — 2026-08-14.** The
four semicircular Highcharts gauges on `/sentiment` are now **two concentric SVG
rings**, each showing **Day / Week / Month** on one dial. Four gauges could show
two horizons; two rings show six readings in less space, and — the substantive
reason — a ring can say **"no data"** where a needle cannot.
- **`ring_svg(arcs, uid, size=280)`** is a pure SVG-string builder (no NiceGUI
  import), mounted with `ui.html` and updated in place via `el.content`. Chosen
  over a Highcharts `solidgauge` and over a CSS conic-gradient: **rounded arc
  caps are impossible in CSS**, and a plain string sidesteps both documented
  `ui.highchart` hazards at once — the ESM-import-map trap (a chart added to a
  page that had none at first render fails `Failed to resolve module specifier
  nicegui-highcharts`) and the `chart.update()` merge/stock-module minefield.
  Precedent: `pages/options/svg.py`.
- **Geometry.** 270° sweep, start 225° / end 135°, measured **clockwise from 12
  o'clock** — so 0 is lower-left, 50 is top, 100 is lower-right, with a 90° gap
  at the bottom that the Week/Month legend lives in. Radii 112/90/68 (outer =
  Day), stroke 13, ticks at r=132, fixed `viewBox="0 0 280 280"`; **`size` sets
  only width/height**, so the dial scales itself and every internal coordinate
  stays in the 280-space. `_value_angle` REQUIRES a pre-clamped 0–100 — past 133
  the sweep exceeds 360° and wraps into a *short* arc that reads as a LOW value.
- Each arc's colour comes from **its own value** via `gauge._ramp_color`, so
  `config/theme.toml [gauge]` still drives the palette. The glow is a **layered
  halo** (a wide translucent copy of the path under a normal-width bright one),
  deliberately **not** an SVG `<filter>` — see the DOMPurify gotcha.
- **`uid` is REQUIRED**: both rings live on the same page and a duplicate DOM id
  makes them collide.
- **`pages/gauge.py` is UNCHANGED** and still serves the options detail-panel
  speedometer (`pages/options/detail.py`), so **the app now carries two gauge
  idioms** — Highcharts needle for a single value in a panel, SVG ring for
  multi-horizon. `rings.py` reuses its `_esc`/`_ramp_color` rather than forking
  them.
- **Page builders** (`pages/sentiment.py`): `sentiment_avg_or_none(snaps, n)` /
  `sentiment_avg` (`WEEK_SNAPS = 5` — the backfill is one snapshot per COMPLETED
  session, so a week is 5 rows, not 7), `sentiment_arcs(live, snaps)`,
  `trend_arcs(derived)`, `_composite_arc_value`, `_trend_arc_value`.
  **`sentiment_30d_avg` was DELETED** (its only caller was a removed gauge; a
  `hasattr` test pins that).
- **The defect class this redesign exists to fix — six instances of ONE failure:
  a missing or garbage input rendering as a CONFIDENT reading.** A non-finite
  composite becoming a full 100 arc (`min(100.0, nan)` is `100.0`, and these
  payloads cross Redis as JSON, which both emits and accepts `NaN`/`Infinity`, so
  a service-side divide-by-zero round-trips intact); an unparseable score
  becoming a maximally-BEARISH 0 via `_safe_float`'s 0.0 default; a NaN sector
  pct becoming **maximum cyclical leadership at full confidence** (measured:
  `score_sector_participation(5, 11, nan)` → `TrendSub(67.27, confidence=1.0)`,
  because `intraday_trend._clamp` is `max(lo, min(hi, v))` and that returns the
  HIGH bound for NaN — hence `compute._finite_pcts`, which DROPS non-finite
  sector moves so the missing sector lowers `n_total` and with it the
  sub-score's confidence, as it should); and **the one that actually fires in
  production** — `compute_7d_trend`/`compute_30d_trend` swallowing their own
  exceptions to return a fully shaped **`score 50.0 / confidence 0.0`** dict, so
  on any proxy blip a good reading is replaced by a confident-looking neutral 50
  and **every absent-key guard misses it**. That is why **`_trend_arc_value`
  keys on CONFIDENCE, not on key presence**. Confidence is a sound
  discriminator here and was verified rather than assumed: `blend_trend`
  weights each sub-score by its own confidence, so the aggregate rounds to 0.0
  only when there was no usable evidence at all — a genuinely neutral but
  well-evidenced 50/50 read scores agg 0.65 and passes straight through.
  `rings._safe_value` is deliberately NOT `gauge._safe_float` for the same
  reason: `None` → track-only + em-dash is how the ring says nothing, and a
  needle has no such state.
- **⚠ OUTSTANDING FOLLOW-UP — the PRICE sub-score has the same NaN exposure as
  the sector one, and it is NOT fixed.** `_finite_pcts` guards only the sector
  input. Measured on the live scorer: an all-NaN read of the structural price
  inputs (`macd_hist`/`rsi`/`adx` at `compute.py:1248-1253`, feeding
  `score_price` with a hardcoded `vwap_pct=0.0`) scores **82.50 — near-maximum
  bullish — at UNCHANGED confidence (0.333)**, where a sane read scores 56.25;
  the same all-NaN read in **`compute_intraday_trend`, the LIVE Day gauge**
  (`compute.py:439-443`) scores **92.50**. Deferred to its own task because the
  fix must cover both call sites with one shared filter.
- **Styling** stays Tailwind-first with **no `ui.add_css`** on the page. Note
  `theme.TILE_3D` is deliberately FLAT (its own comment: "a hairline border +
  12px radius, **NO bevel or drop shadow**", from the Deep Slate flattening) and
  was **not** redefined — the Signals tiles' glow tokens are LOCAL to
  `sentiment.py` (`_tone_classes`/`TONE_CLASSES`). The rings already carry a
  halo, so **the page is now mixed**: two glowing elements against a token
  vocabulary that says flat. A third would mean the theme has moved in practice
  and `theme.py` should be changed to match rather than routed around again.
  Reactive recolours swap via `.classes(remove=TONE_*_CLASSES, add=…)` — one
  remove-set per element type (value text / tile shell / rule / dot), since a
  partial set stacks across the version-poll repaint.
- **`_word_tone` — BIAS and SIGNAL carry `live_composite.signal_band`'s OWN
  vocabularies** (`Long / Neutral / Cautious / Short` and `Strong Bull … Strong
  Bear`), which are **NOT** the composite's `bias` field. `bias_color` only
  substring-matches bull/bear, so "Long" and "Short" read amber forever. Each
  tile now colours from its own word, and **`bias_text_class` delegates to
  `_word_tone`** so the headline under the ring can no longer contradict the tile
  beside it — it was rendering "7.28 · Long" in amber directly above a green
  "Long".
- **`velocity` and `divergence` are rendered again.** The service had been
  computing and publishing both on **every** refresh with **no renderer at all**
  since the intraday graphs replaced the old text block — a silent regression, an
  accident of that layout change, not a decision. `velocity_lines(derived)`
  returns `{text, flag, divergence}`; the flag and the divergence note hide when
  empty (empty means "no regime break", not "unknown").
- **Test isolation the change forced.** An autouse `conftest` fixture now resets
  `_SECTOR_PCTS_CACHE` / `_TREND_7D_CACHE` / `_TREND_30D_CACHE` **before and
  after** every sentiment_svc test — without it, any test stubbing `_fetch_closes`
  leaves its FIXTURE values in those module globals and a later un-monkeypatched
  self-fetching call silently consumes them (a probe `compute_30d_trend()` scored
  its sector sub-score 73.33 off stale stub data with ZERO fan-outs). The suite
  only stayed green because `pytest-randomly` isn't installed and the ordering
  happened to be kind. Design/plan:
  [design](docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md) /
  [plan](docs/plans/2026-08-14-sentiment-trend-ring-graphics-plan.md).


**Live intraday + bridge (DONE).** `sentiment-dashboard/live_composite.py`
`compute_live(schwab, sector_data)` computes a **live** composite from current
quotes reusing the pure scoring modules (the live analog of
`history_backfill._score_one_day`); `build_bridge_payload(...)` + `bridge.write_bridge`
publish `shared/sentiment_bridge.json` (consumed by `options-scanner/regime_filter`).
The **GEX collector** (`options-scanner/gex_collector.py`) publishes the bridge each
5-min cycle via a **subprocess** (`publish_bridge.py`, sentiment dir on `sys.path[0]`
to dodge the `scoring` package-vs-module collision) — independent of the webgui. The
webgui page's **headline always uses the live composite** (`compute_live`; the
`is_rth` flag now only labels the date as "live intraday" vs "latest — market
closed"), falling back to the backfill snapshot only if the live compute fails;
**backfill feeds only the 30-day history chart**. This keeps the web matched to
the legacy v4.3 methodology around the clock — Put/Call uses cap-weighted sector
P/C (not `$CPCE`) and Rotation uses dual-momentum (not the blended `compute_rotation`);
off-hours, Put/Call/Breadth may read 0 when there's no option/market volume, exactly
as the legacy "Fetch Live" does. Component labels match the legacy
("Put/Call (sectors)", "Market Breadth", "Sector Performance"; VIX value `T{t}-1D{d}-S{s}`).
The page also publishes the bridge on each load. Verified live: `compute_live`
reproduces the legacy component scores (VIX T8-1D1-S3=5, Breadth 10, Rotation 7
dual-momentum) and `regime_filter.evaluate_regime()` reads the written bridge.
Design/plans: [live-bridge](docs/plans/2026-06-14-live-sentiment-bridge-design.md) +
[web/legacy reconcile](docs/plans/2026-06-15-sentiment-web-legacy-reconcile-design.md)
(+ `-plan.md` files).

**Next session — remaining pages (Phase 3.3–3.5 of the webgui plan):**
- **Trade** (`/trade`): **DONE — 2026-06-16 (3-tier, `services/trade_svc` :8213).**
  See "Trade page (`/trade`) — DONE" below.
- **Portfolio** (`/portfolio`): **DONE — 2026-06-16 (3-tier, `services/portfolio_svc`
  :8212).** See "Portfolio page (`/portfolio`) — DONE" below.
- **Driver** (`/driver`): **DONE — 2026-06-16 (3-tier, `services/driver_svc` :8214).**
  See "Driver page (`/driver`) — DONE" below.
- Reuse the page pattern above; verify each engine function's real signature in
  the copied module before wiring (explorations of source can drift from copy).
- Optional follow-ups: (none outstanding for the Simulator — the **Replay** tab
  was migrated 2026-06-20: `compute.sim_replay` + `sim_replay` command +
  `cache:options:sim_replay` + the page's Replay tab. See "Simulator Replay tab"
  below.) (The Gamma intraday-heatmap **collector** now runs inside `options_svc`
  — see below — so the heatmap populates all session whenever the service is up.)

**Gamma intraday-heatmap collection (DONE — 2026-06-15; expanded 2026-06-18).** Intraday GEX history
(`gex_history.db`, read by the Gamma strike×time heatmap) is now collected by the
**options service** itself, not a separate window. `services/options_svc/scheduler.py`
`gex_due()` fires once per 1-min slot within 08:00–15:20 CT on trading days (mirrors
`gex_collector`'s window/cadence); the tick runs `handlers.collect_gex_history` →
`compute.collect_gex_snapshots`, which reuses `options-scanner/gex_collector.poll_once`
(engine compute + `gex_history_db.insert_snapshot`) VERBATIM with the shared
`_proxy.schwab_py_client`. It takes the collector's advisory lock
(`data/gex_collector.lock`) so a manually-run standalone `gex_collector.py` defers.
**Symbol universe + cadence (2026-06-18):** `poll_once` iterates
`gex_collector.collection_symbols()` = the index base (`$SPX`/`$VIX`/`SPY`/`QQQ`) ∪
`watchlist.get_scan_symbols()` (`Top 20.xlsx`), deduped/order-preserving, defensive
fallback to the base on watchlist failure — so the heatmap has live data for every
watchlist symbol. The poll interval dropped **5→2 min** (`POLL_INTERVAL_MIN=2`,
`scheduler._GEX_INTERVAL_MIN=2`, `gex_status.STALE_AFTER_SEC=240` — a
`test_scheduler.py` drift-guard asserts these stay in lockstep). The Gamma page's
symbol **dropdown** reads `cache:options:gamma_symbols` (= collected universe minus
`$VIX`, `$SPX` first), published once at scheduler startup by
`handlers.publish_gamma_symbols`. Term-structure collection stays SPX-only. Design/plan:
[design](docs/plans/2026-06-18-gex-watchlist-gamma-dropdown-design.md) /
[plan](docs/plans/2026-06-18-gex-watchlist-gamma-dropdown-plan.md).
**Root cause this fixed:** previously the only writer was the standalone
`gex_collector.py` window launched by `start_all.bat`; when that window died
(closed / sleep / double-launch lock contention) collection stopped silently and the
heatmap froze at the first snapshots ("no data past the first hour"). `start_all.bat`
no longer launches a separate collector window (the standalone script remains a manual
fallback). NOTE: this path does NOT republish the sentiment bridge (the old collector
loop did); `sentiment_svc` already republishes the bridge every 120 s, so the bridge
is unaffected.
**Off-hours display persistence (2026-06-24).** Collection still stops at ~15:20 CT,
but the Gamma **display** now holds the last session's candles + heatmap until the
**next trading day's midnight CT**, then clears (Fri persists through the weekend /
holidays until the pre-session midnight). Pure helpers
`scheduler.active_session_date(now)` (today once collection starts at 08:00 CT on a
trading day, else the most recent prior trading day) drives it — there is NO overnight
blanking, so the charts show PRE- and POST-market (`gamma_cleared` was removed); `gex_history_db.load_date_with_grid(conn,
symbol, view, date)` loads a prior session's rows by explicit local date
(`load_today_with_grid` now delegates to it). `compute.gamma_snapshot` returns
`None` (→ handler caches a graceful-empty view) in the cleared window and loads the
**active session date** for the heatmap; the candles re-compute from the live chain
(which off-hours returns the last session's data). DB-backed, so it survives a
service restart. **Term-structure** collection stays SPX-only, but the Gamma page's
**Term view** fetches the **next 5 expirations regardless of cadence** at render
(`compute._term_chain` widens the chain window — weekly/monthly-only names show 5
columns, not 1).

**Gamma Analyze — Claude infographic + auto-run (DONE — 2026-06-27).** The
`/options/gamma` **Analyze** button evolved from a copy-paste prompt dialog into a
live Claude call that renders an **infographic** in a new browser tab (and runs
itself four times a day). All in `services/options_svc/compute.py` (Tier-2) +
`webgui/main.py` route + `webgui/pages/options/gamma.py` (Tier-1). Pieces:
- **Forced tool-use call.** `compute.gamma_analyze(client=None, label=None)` bundles
  the live $SPX/SPY/QQQ GEX/Charm/DEX/Vanna blocks (`_gamma_blocks_for` →
  `build_summary_prompt_bundled`) and calls **Claude Sonnet 5** (`_ANALYZE_MODEL`,
  `thinking={"type":"disabled"}`, `max_tokens=1500`) forcing the **`submit_analysis`**
  tool (`_ANALYZE_TOOL`) — the reply is one structured tool_use block, never free
  text. `_parse_analysis` normalizes it (total over adversarial input, mirrors
  `decider.parse_decision`). The `anthropic` import is LAZY; the key resolves in
  `compute._anthropic_api_key` (env `ANTHROPIC_API_KEY` → gitignored
  `shared/anthropic_key.txt`) — **options_svc does NOT import driver_svc** (kept local
  to avoid the cross-app collision).
- **Infographic render (pure, testable).** `analyze_infographic_html(data, subtitle)`
  → a regime banner + **bias meter** (`_bias_meter_html`, −100…+100, sign-colored
  marker); a **per-index card** (`_index_card_html`) = a **price-level ladder**
  (`_ladder_svg`: spot vs gamma flip / call+put walls / expected-move band, with
  **label de-collision** so clustered levels stay readable) + **metric tiles**
  (`_metric_tiles_html`) + note + a **per-symbol what-if** (`_whatif_html`: ▲ rally /
  ▼ sell-off / ▬ chop); and a bottom **"Why is this happening"** section. Wrapped by
  `_analyze_doc` (the standalone dark doc + `_ANALYZE_CSS`). Output carries **no
  disclaimers** (system-prompt-enforced).
- **Code-authoritative Exp. move.** The engine's `calc_expected_move_from_chain` is a
  **0-DTE remaining-hours-to-close** EM (`hours_left` clamps to 0.1h off-hours / at the
  close → collapses to ~0; the bug that surfaced SPX EM ≈ 3). `compute._session_expected_move`
  computes a stable **1-day** EM (`spot · ATM_IV · √(1/365)`, reusing the engine's
  static `_find_nearest_exp_key`/`_get_atm_iv`) — used both in the prompt and as a
  per-symbol **override** of the model's copied value, so the displayed EM is
  engine-computed, not AI-echoed.
- **4×/day auto-run.** `scheduler.analyze_slot_due(now, ran_slots)` fires once per
  trading day per slot (CT: premarket 08:00 / open 08:48 [~18 min after the 09:30 ET
  open] / midday 11:30 / close 14:58 → 09:00/09:48/12:30/15:58 ET) within a 20-min
  grace (tolerates a missed tick / mid-window start, no stale backfill); the loop
  latches `analyze_ran` BEFORE the blocking call so a slow call can't double-fire.
  `handlers.run_scheduled_gamma_analyze(bus, slot)` runs `gamma_analyze(label=…)` and
  caches under that slot's **own key** (`CACHE_GAMMA_ANALYZE_SCHED` =
  `cache:options:gamma_analyze_{premarket,open,midday,close}`) — **separate** from the
  ad-hoc `cache:options:gamma_analyze` so a scheduled run never trips the page's
  `_watch_analyze` (which auto-opens a tab). The doc subtitle is stamped with the slot
  + CT time.
- **Serving + page.** `webgui/main.py` `@app.get("/options/analyze")` serves the cached
  HTML raw (`analyze_html`); `?slot=premarket|open|midday|close` (`analyze_view_for`)
  serves the auto-briefings, no slot → the ad-hoc result (mirrors `/options/explain`).
  `gamma.py` `_watch_analyze` opens `/options/analyze?v=<version>` in a new tab on the
  version-poll (like `_watch_explain`); a row of **Auto briefings** buttons opens each
  slot's `/options/analyze?slot=…` (enabled once that slot's version is present).
- **Graceful degradation everywhere** — no live chains (market closed) / no API key /
  API error / no tool reply each return a readable HTML page so the tab always opens.
- Tests: `services/options_svc/tests/{test_compute,test_handlers,test_scheduler}.py`
  (tool-use render, EM override, parse defensiveness, slot cadence, scheduled-cache
  isolation) + `webgui/tests/test_analyze_route.py`. Verified live end-to-end (real
  Claude call → infographic; EM SPX 2.96→45.9 / SPY 0.27→4.22 / QQQ 0.52→8.0).

**Gamma briefing history — store + CLI utility + in-app viewer (DONE — 2026-07-08).**
Every briefing above is now persisted so past briefings can be browsed/regenerated.
The design decision (deliberate): **store the STRUCTURED analysis payload, regenerate
the report on demand** — compact, queryable, and future-proof (old briefings re-render
in the current infographic design; the raw GEX numbers already live in
`gex_history.db`, so only the AI's structured read is kept). Pieces:
- **Store** `options-scanner/gamma_briefing_history_db.py` (Tier-3 SQLite,
  `repo_paths.GAMMA_BRIEFING_DB` = `options-scanner/data/gamma_briefings.db`). One row
  per **`(date, slot)`** (scheduled slots are unique/day → re-run REPLACEs; ad-hoc/
  manual use time-stamped slots like `adhoc-1842` so each is kept). Columns: date,
  slot, generated_at, symbol_scope, model, **bias**, **headline** (pulled out for
  cheap trend queries) + **`analysis_json`** (the full structured dict = source of
  truth). `connect`/`insert_briefing`/`get_briefing`/`briefings_for_date`/
  `list_briefings`/`purge(keep_days)`; every fn takes an explicit conn for temp-DB tests.
- **Persistence** `handlers._persist_briefing(res, slot, now)` (best-effort; only runs
  with a real `analysis` — degraded no-chains/no-key/error pages are skipped; never
  raises) wired into `run_scheduled_gamma_analyze` + the ad-hoc `gamma_analyze` command.
- **Report builder** PURE `compute.analyze_history_doc(briefings, title)` — combines N
  stored briefings into one standalone doc (each re-rendered via
  `analyze_infographic_html` under a date/slot header), reusing `_ANALYZE_CSS`.
- **In-app viewer.** `handlers.publish_gamma_briefing_index` publishes the metadata
  index **`cache:options:gamma_briefings`** (startup + after each persist); the
  **`gamma_history`** command (`run_gamma_history(bus, date, slot=None)`) regenerates a
  date's (or a single slot's) report → **`cache:options:gamma_history`**, served raw at
  **`/options/gamma-history`** (`webgui/main.py`). The `/options/gamma` page's
  **History picker** — a date dropdown (from the index via the pure `history_dates`) +
  a slot select (All / the four slots) + **Open** — enqueues `gamma_history` and opens
  the regenerated report in a new tab on the version-poll (mirrors `_watch_analyze`).
- **CLI utility** `services/options_svc/gamma_briefing_report.py` (run MANUALLY, never
  in a request path): `--list [--days N]` / `--date YYYY-MM-DD [--slot S]` (single day,
  slots combined) / `--range START END` / `--generate [--slot L]` (fresh run via
  `compute.gamma_analyze` → store → report; needs the proxy + ANTHROPIC key). Writes
  HTML under `options-scanner/data/gamma_reports/` (or `--out`).
- **Restart `options_svc`** so persistence + the index publish go live (the DB starts
  empty and fills going forward). gamma_briefing_history_db **7** + options_svc
  handlers/scheduler/compute + webgui **689** green; verified live end-to-end (index
  published, picker populated + Open→regenerate→serve, CLI `--list`/`--date` combined
  report). Built per-layer TDD.

**Options GUI polish batch (DONE — 2026-06-16).** A set of UI/UX fixes across the
Options section (design/plan:
[design](docs/plans/2026-06-16-options-gui-polish-design.md) /
[plan](docs/plans/2026-06-16-options-gui-polish-plan.md)):
- **Nav dropdowns persist** across navigation — `webgui/main.py` stores each
  `ui.expansion` open/closed state in a module-level `_NAV_OPEN` dict (single-user,
  like `_CACHE`); first visit still auto-opens the active group.
- **Scanner**: signals colored by quality via `score_zone_color` (zones match the
  speedometer) on a `body-cell-composite_score` slot; the VIX term label is plain
  English via `term_text` ("VIX term: Contango (near-term calm) · as of 1:32 PM");
  newly-appeared signals get a **NEW** badge via a session diff (`mark_new`,
  page-side, resets on reload — both 0-DTE + Swing tables).
- **Paper Trades**: the detail panel now re-renders for the selected row on each
  data refresh (`paper.py` tracks `sel_id`, re-calls `detail_panel.update`).
- **Captured Signals**: drift shown as `x.xx` (numeric value kept for sort, a
  `body-cell-score_drift` slot renders `toFixed(2)`); rows colored by
  recommendation (`rec_color`: HOLD amber / TAKE_PROFIT green / CUT red).
- **Calculator**: P&L grid range is symmetric about spot and widened to span the
  strikes — `compute.symmetric_price_range` in `calc_compute` (engine untouched).
- **Symbol inputs auto-select on focus** (calculator/gamma/simulator/swing) via the
  shared `webgui/pages/options/inputs.py` `select_all_on_focus` helper.
- **Gamma status bar**: `compute.gex_status_view` (collector status via
  `gex_status.classify_collector_status` + `gex_history_db.last_snapshot_age`, plus
  last/next 1-min scan within 08:00–15:20 CT, reusing the scheduler's `_GEX_*`
  constants) is published each 30 s tick by `handlers.publish_gex_status`
  (`cache:options:gex_status`); `gamma.py` shows **Collector / Last scan / Next scan**
  alongside the existing "Next refresh" countdown.
- Pure transforms are unit-tested (webgui + options_svc suites).

> **Paper auto-manage (DONE — supersedes the old "manual-only" TODO).** The
> `options_svc` scheduler reprices + auto-closes paper positions on its own. **Two
> distinct cadences (changed 2026-07-10):** the **MANUAL Paper Portfolio** runs
> **entry + manage once at the top of each hour, 09:00–14:00 CT** (last run 14:00 /
> 2pm; **NO 15:00 run** at the regular-session close) — `scheduler.paper_cycle_due`
> (trading days only, once-per-hour within a 20-min grace, mirrors
> `analyze_slot_due`) → `handlers.run_paper_entry_and_manage` (opens new paper
> trades from current captured signals via `compute.run_entry_cycle`, guarded on an
> existing account + its own try/except so an entry failure can't skip manage, then
> `run_manage_and_refresh`). The **isolated DRIVER paper account** stays on the
> old **5-min** `manage_due` slot (`run_driver_manage_and_refresh`). Both windows
> are trading-day/market-hours gated. The "Run Manage Cycle" button is still a
> manual trigger of the manage cycle. (Tick cadence reference: each 30 s scheduler
> tick also runs `refresh_header` + `publish_gex_status`; the 2-min GEX collect +
> 5-min driver manage are slot-gated within their CT windows. **Trade-off to know:**
> the manual account's live P&L + target/stop auto-close now update **hourly**, not
> every 5 min.)

**Gamma panels / walls / flicker batch (DONE — 2026-06-16).** Four fixes from a
live-screenshot review (design/plan:
[design](docs/plans/2026-06-16-gamma-panels-walls-flicker-design.md) /
[plan](docs/plans/2026-06-16-gamma-panels-walls-flicker-plan.md)):
- **Proportional panels**: `gamma.panel_flex(n_cols)` sets the bar/heatmap column
  flex ratio from the intraday snapshot count (heat fraction lerps 0.28→0.70 over
  ~82 five-min slots), so the heatmap expands and the bars shrink as the session
  fills in. Term view → bars full width, heatmap hidden.
- **GAMMA dead space**: `gamma.significant_strikes(bars, frac=0.03)` feeds the
  shared y-range from strikes with |net| ≥ 3 % of peak, cropping GEX's near-zero
  edge strikes (other views were already tight). Both panels share the range.
- **Flicker**: the two Highcharts elements are created **once** and updated in
  place (`el.options = …; el.update()`); `_render_view` no longer
  `clear()`s/rebuilds the canvas. Message labels toggle via `set_visibility`.
  (Now `ui.highchart`; the bar↔Term kind switch recreates via `_set_chart`.)
- **Single walls**: `services/options_svc/compute.gamma_walls` returns one Put +
  one Call wall via the engine's `get_directional_walls` (call = max-call-GEX
  strike above spot, put = most-negative-put-GEX below) instead of the old
  `get_gex_walls`/`get_dex_walls` top-5; the page renders them unchanged. DEX
  per-strike map remapped `dex`→`gex` for the picker.

**Trade page (`/trade`) — DONE (2026-06-16, born 3-tier — Phase 4).** The Trade
Analyzer was built directly on the 3-tier model (no in-process stage). New
service `services/trade_svc` (:8213, `SERVICE_PORTS["trade"]`), **on-demand only
(no scheduler)** — the page enqueues an `analyze` command on `cmd:trade`; the
service computes and writes `cache:trade:analysis` (one latest-result view, like
sim/calc) + publishes `events:trade:analysis`; the page version-polls and
repaints (persists across nav). Pieces:
- **Contract** `shared/contracts/trade.py:TradeAnalysis` — validates the analyze
  envelope (symbol + verdict/momentum/sector sub-dicts) before caching.
- **`trade_svc/compute.analyze(symbol)`** ports the legacy desktop
  `trade_analyzer.py` `analyze()` flow (the un-copied orchestration): fetch MTF
  data via the proxy (`_proxy.schwab_client`; 1/5/15/60-min + daily, SPY +
  sector-ETF daily), compute indicators **reusing `shared/analysis_lib/technical`**
  (`calculate_ema_alignment`/`calculate_rsi`/`calculate_adx`/`calculate_macd`/
  `calculate_vwap`/`calculate_relative_volume`/`calculate_volume_profile`), build
  `PositionInputs`/`InvestorInputs`, and score the copied
  `trade-analyzer/src/analysis/recommendation` verdict engines. **Defensive**
  (degrades to an `errors` payload, never raises). `technical` is imported
  **standalone** (its dir on `sys.path`) to dodge the `shared.analysis_lib`
  package `__init__` (which eagerly imports a broken `schwab_client`); safe
  because the service is its own process (same isolation `sentiment_svc` uses for
  `scoring`). Symbol→sector via a built-in large-cap map (`_SYMBOL_SECTOR`) with a
  **neutral** SectorStrength fallback when unknown.
- **`trade_svc/handlers.analyze`** runs compute → `TradeAnalysis` gate → cache +
  publish; `handle_command` dispatches `analyze`. **`trade_svc/app.py`** =
  `make_app("trade", command_handler=…)` (no scheduler).
- **Page** `webgui/pages/trade.py`: symbol input (+Enter) → Analyze; renders a
  header (symbol/price/bias/vol), two verdict cards (verdict colored BUY-green/
  HOLD-amber/SELL-red, score, top reasons, ⛔ hard gates, expandable factor
  breakdown table), MTF-alignment card, momentum strip, sector card. Pure builders
  (`verdict_color`/`bias_color`/`momentum_rows`/`breakdown_rows`/`alignment_rows`)
  unit-tested in `webgui/tests/test_trade.py`.
- **Fundamentals wired via the proxy (2026-06-16).** `compute.analyze` fetches
  Schwab fundamentals through a new proxy endpoint
  `GET /instruments?symbol=X&projection=fundamental`
  (`SchwabProxyClient.get_fundamentals` → unwraps `instruments[0].fundamental`)
  and parses them with `parse_schwab_fundamentals`. `InvestorVerdict` now runs on
  real data and `fundamentals_available` = `Fundamentals.is_sufficient()`; the
  page shows a **Fundamentals card** (P/E, PEG, rev/EPS growth, ROE, margin
  trend) when available, else the insufficient-data note. **Parser is a superset**
  (`trade-analyzer/src/analysis/fundamentals.py`): the *real* Schwab fields
  (`revChangeTTM`/`epsChangePercentTTM` in percent→fraction, `returnOnEquity` as
  percent via a `>2` magnitude heuristic, `operatingMarginTTM` vs `MRQ` for the
  margin trend) are primary, the legacy speculative names are fallback (all old
  tests stay green). The instruments payload has **no** next-earnings date / EPS
  surprises / guidance / FCF, so those degrade to None (the Position earnings gate
  never fires; `days_to_earnings` is None). Fetch is defensive — a proxy/parse
  failure degrades to insufficient-data HOLD, never raises.
- Tests: `services/trade_svc/tests` (compute/handler/app) + `webgui/tests/test_trade.py`.
  Design/plan: Phase 4 of the [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**Validated swing evaluation (Trade page) — DONE (2026-06-28).** The `/trade`
**Position** verdict's hand-weighted, never-validated swing scoring is replaced by a
**backtested, IC-weighted cross-sectional factor model** whose weights are learned from
forward returns. Investing (months+) is **deferred** — it can't be backtested without a
point-in-time fundamentals source. Honest framing: the model is *validated* (it shows a
small **positive out-of-sample IC** + a calibrated quintile spread), not *guaranteed* —
the edge is thin and regime-dependent. Architecture = **offline fit → versioned artifact
→ online score** (the C-ready shape from the design: a single regime key `"all"` today;
`"trend"/"chop"/"highvol"` drop into the same loader/scorer later). Pieces:
- **PURE factor library** `trade-analyzer/src/analysis/factors.py` — each factor is
  `(daily_df) → pd.Series` over a daily-OHLCV frame, **sign-corrected so higher =
  bullish**, **causal** (the value at bar *t* uses only data ≤ *t* — no look-ahead).
  Winsorization/standardization are NOT per-factor; they happen **cross-sectionally at
  scoring** (`zscore_by_date`, across symbols per date → no temporal leakage). The live
  value is the Series' last element, so the SAME code feeds the backtest and the live
  scorer (no drift). The 10 registered factors: **mom_12_1** (12-1 intermediate
  momentum, skip-month), **mom_6_1**, **pth** (price ÷ 252-day high — George & Hwang
  anchoring), **str_5d** (short-term 5-day reversal, sign-corrected), **vol_adj_mom**
  (3-mo return ÷ realized vol), **trend_quality** (distance above the 50/200-EMA stack),
  **low_vol** (−60-day realized vol), **rs_spy** (63-day excess return vs SPY),
  **rs_sector** (63-day excess vs the sector ETF), **turnover** (volume ÷ 63-day avg —
  the conditioning var). The `FACTORS` registry is the single source of truth; the
  **harness's IC decides which earn weight** (not the hand-picked list).
- **OFFLINE harness** `trade-analyzer/src/analysis/backtest.py` (pure — operates on a
  `(date,symbol)`-MultiIndex panel + forward Series, no I/O): `factor_ic`
  (per-date cross-sectional **Spearman rank IC** → mean_ic/icir/n_days; ICIR only
  trusted with ≥5 IC-days + real dispersion), `quantile_spread` (top-minus-bottom),
  `zscore_by_date` (cross-sectional winsorize @ 2/98 + standardize, look-ahead-free),
  **`signed_ic_weights`** (the production weighter: `weight_k = mean_ic_k / Σ|mean_ic|`,
  **keeping the sign**, above an n-independent noise floor — so a wrong-sign-but-
  predictive factor like low_vol carries a **NEGATIVE** weight and contributes with the
  correct sign; chosen over ICIR-/t-stat-weighting because those are n-dependent and
  unstable across small per-fold samples), `composite`, **`walk_forward`**
  (rolling train→test, weights fit per train window, composite OOS IC on the unseen test
  window; train/test never overlap), `calibrate` (bucket composite into quantile bands →
  per-band score range + mean forward + hit-rate `P(fwd>0)`, **isotonic-smoothed** so a
  higher-ranked band never shows a lower stat). The orchestrator
  **`trade-analyzer/fit_swing_model.py`** (run manually/periodically, **NEVER imported by
  a service**) pulls ~78 liquid symbols' (curated `UNIVERSE_SECTOR` → sector ETF) **5-yr**
  daily history via the proxy (concurrent), builds the panel with **20-day forward
  EXCESS-return-vs-SPY** labels (the prediction target — factors are causal so the future
  H-bar label is legitimate), runs the engine (train/test/step **378/63/63**), and writes
  the artifact + a markdown research report.
- **Artifact** `trade-analyzer/data/swing_model.json` (gitignored under `data/`; path
  `repo_paths.SWING_MODEL`, report `SWING_MODEL_REPORT`) — `version` (the fit date),
  `fit_universe_n`, `horizon`, and per regime: signed **`weights`**, **`factor_ic`**
  (mean_ic/icir/n_days per factor), the cross-sectional **`norm`** (per-factor
  time-averaged winsorized cross-sectional mean/std — the basis the calibration was built
  on), the score→outcome **`calibration`** (5 quantile bands → score range / mean_fwd /
  hit_rate / n), and **`oos_ic`** + `oos_ic_by_fold` + `n_folds`.
- **LIVE scorer** `services/trade_svc/swing_model.py` (on-demand, defensive → returns
  `None` so `analyze()` falls back to the legacy verdict on ANY failure): loads the
  artifact, z-scores the symbol's current factors **CROSS-SECTIONALLY against the current
  universe snapshot** (PRIMARY — re-centered to today's regime, matching how the per-date
  calibration was built; the artifact's time-averaged norm is a FALLBACK only, used when
  the snapshot is too thin, <5 names), **clips z to ±3** (`Z_CLIP` —
  matches the fit's per-date 2/98 winsorization; stops a live outlier like a turnover
  spike hijacking the signed composite), `composite = Σ signed_weight × z`, then reads the
  **calibration band** containing the composite → **BUY** (top band) / **SELL** (bottom) /
  **HOLD**, a band-quantile **percentile**, the band's expected forward return + beat-SPY
  hit-rate, and per-factor contributions (z · weight · contribution · historical IC).
  `analyze()` fetches **2-yr daily** so every long-warmup factor (mom_12_1 needs 273 bars;
  pth/low_vol roll 252) populates at the last bar.
- **Fix — "Position always BUY" (2026-06-28):** the live scorer originally used the
  artifact's **time-averaged** norm as the PRIMARY z-basis, which does NOT re-center to the
  current regime. In this elevated-momentum/-vol bull period every symbol's z shifted
  positive (most starkly `low_vol`: tiny norm std × big negative weight → a saturated ±3 z
  → ≈ +1.0 contribution that dominated), so the composite cleared the top band and **every
  symbol scored BUY**. Fixed by re-centering to the **current cross-section** (the snapshot,
  PRIMARY) — matching the per-date calibration basis — and **widening** that snapshot to the
  artifact's `fit_universe` (~78 names, was ~17) for stable z's. Verified live: the universe
  now scores ≈ **8 BUY / 49 HOLD / 8 SELL** (was ~all BUY); NVDA/PLTR flipped BUY → HOLD.
- **Contract / cache:** additive optional **`swing_model`** block on `TradeAnalysis`
  (→ `cache:trade:analysis`); `compute.get_universe_snapshot()` lazily rebuilds a daily
  **`cache:trade:universe_factors`** snapshot ({factor: [values across the artifact's
  **`fit_universe`** ~78-name fit cross-section]}) as the PRIMARY cross-sectional scoring
  basis — `_swing_universe()` reads `fit_universe`, falling back to the smaller `_MK_UNIVERSE`.
- **UI** `webgui/pages/trade.py` (Position card): the validated swing verdict is the
  **headline** + a calibrated outcome line (e.g. `90th pctile · +1.3% excess / 20d · 52%
  beat-SPY` via `swing_headline`), a **"Why — validated factors"** expander (per-factor z
  / weight / contribution / historical IC + the model version & OOS IC via
  `swing_contrib_rows`/`swing_model_meta`), and the **legacy heuristic** verdict tucked
  into a collapsed **"Legacy heuristic"** expander (`_legacy_verdict_body`). Falls back to
  the legacy body verbatim when `swing_model` is absent. Investor + Markov cards
  unchanged — **the Markov card still forecasts the legacy technical-momentum
  `composite_daily`**, NOT the validated composite (a separate lens; a documented
  coexistence, not a bug).
- **Validated result (current fit, `version` 2026-06-28):** fit universe **78** symbols,
  horizon **20d**, **13** walk-forward folds. Composite **OOS IC ≈ +0.0367** — but **5 of
  13 folds are NEGATIVE**, so the edge is thin and **regime-dependent**. Calibration: top
  quintile (band 4) ≈ **+1.35% / 4 wk at 52.3% beat-SPY**, bottom (band 0) ≈ **−0.80% /
  43.3%**. Signed weights (the ONLY factors that cleared the |IC| floor): **low_vol
  −0.34** (reclaimed with a NEGATIVE weight — high-vol names outperformed in this 5-yr
  large-cap bull period, IC −0.066), **mom_12_1 +0.21**, **mom_6_1 +0.17**,
  **trend_quality +0.12**, **rs_sector +0.08**, **turnover +0.07** (pth / str_5d /
  vol_adj_mom / rs_spy fell below the floor → weight 0).
- **Honest caveats (state these):** the edge is small + regime-dependent; it leans on
  **low_vol's inverted sign** reflecting this bull-ish large-cap period (it could flip);
  **survivorship bias** (the fit universe is today's liquid survivors) + **regime
  non-stationarity** (a 5-yr fit may not hold forward); the LIVE cross-section
  (~watchlist) is thin vs the fit universe. Validation reduces self-deception, it does not
  guarantee forward performance — **re-run `fit_swing_model.py` periodically**.
  **Regime-conditional weighting (Option C)** is the planned next step (same harness, new
  regime keys); ML (B) is gated on universe expansion.
- Tests: factor library + harness + live scorer + contract + page builders are unit-
  tested (TDD by layer, the design's acceptance gate = positive OOS IC + a meaningful
  spread on real data). Design/plan:
  [design](docs/plans/2026-06-22-swing-validated-evaluation-design.md) /
  [plan](docs/plans/2026-06-22-swing-validated-evaluation.md).

**Markov 2.0 (Trade page) — CARD REMOVED from the UI (2026-06-28); engine retained.**
The Markov Forecast card was **deleted** from `/trade`. It forecast the **LEGACY**
technical composite, so it contradicted the validated Position read (it showed
"Strong-Bear" while the validated model said BUY on the same 1–8 wk horizon).
`compute.analyze()` no longer builds the block either — it was a wasted pooled-prior
rebuild + history fetch per request for a block nobody rendered (pinned by
`test_analyze_does_not_build_markov_block`). **Retained but unused:** the PURE engine
(`trade-analyzer/src/analysis/markov.py`, 34 tests), the compute helpers
(`reconstruct_daily_composite`/`_symbol_band_series`/`build_pooled_prior`/`get_prior`/
`build_markov_block`), and the additive `markov` contract field. **Reviving it against
the VALIDATED composite is NOT a small change:** that composite is a per-date
CROSS-SECTIONAL score, so a symbol's history is *not* reconstructable live — the OFFLINE
fit would have to emit per-symbol transition matrices, and non-fit-universe symbols would
fall back to a generic pooled forecast. The historical description follows.
A probabilistic, forward-looking
layer on the `PositionVerdict`: model the composite score as a 5-state Markov chain,
surface where it's heading, and apply a bounded tilt to the score (the BUY/HOLD/SELL
label is untouched). Pieces:
- **States = composite-score bands** anchored at the decision boundaries (S1
  Strong-Bear `[-100,-40)` = SELL · S2 Weak-Bear `[-40,-15)` · S3 Neutral `[-15,15)`
  · S4 Weak-Bull `[15,40)` · S5 Strong-Bull `[40,100]` = BUY), so a forecast reads
  directly as P(cross into BUY/SELL).
- **PURE engine** `trade-analyzer/src/analysis/markov.py` (no I/O): `classify_band`,
  `count_matrix` (NaN/None breaks the chain), `pooled_prior`, `shrink`
  (Dirichlet-multinomial, α≈30), `project` (`dist·P^n`), `forecast`
  (P(BUY)/P(SELL)/E[score]/persistence/**stationary via power-iteration** — robust to
  reducible chains), `row_confidence`, `drift_tilt` (clamped ±12, confidence-weighted).
- **Daily score reconstruction** `trade_svc/compute.reconstruct_daily_composite(daily,
  spy, sector_hist)` builds a parallel **"Markov base score" (`composite_daily`)** from
  ONLY daily-reconstructable factors (the live verdict's intraday VWAP/rel-vol/MTF-EMA
  can't be rebuilt for past bars), renormalized to 100, fully vectorized; a missing-close
  bar → NaN (no observation). The chain runs on `composite_daily`, which also **dissolves
  the feedback loop** — the tilt is added to the displayed `composite_full`, never to
  `composite_daily`, so it can't feed back into the matrix.
- **Hybrid matrix:** per-symbol day-to-day counts `shrink`-blended toward a pooled prior
  built across a curated 17-symbol universe (`build_pooled_prior` / `get_prior`, cached at
  `cache:trade:markov_prior`, lazy daily refresh, uniform fallback on failure).
- **Wiring:** `build_markov_block` runs **defensively** inside `compute.analyze()`
  (any failure → `markov: None`, verdict unchanged) and rides an additive optional
  `markov` block on the `TradeAnalysis` contract → `cache:trade:analysis`.
- **Page** `webgui/pages/trade.py`: the Markov Forecast card sits in the **verdict row as
  the third equal-width card** alongside **Position · 1–8 wk** and **Investor · months+**
  (all `flex-1 min-w-[280px]`, `items-stretch` — three equal frames in one row, wrapping on
  narrow screens). The row and its three cards are **persistent** and the two verdict cards
  are **refilled in place** (`_fill_verdict_card`) so the Markov card's Highcharts element
  is never destroyed by a `clear()` (it's built once per the ESM-import-map gotcha, with an
  explicit `chart.height` + reflow-on-show, updated in place). The card holds a band chip, a
  stacked-area band-probability-over-horizon chart (now/5/10/20d), per-horizon
  P(BUY)/P(SELL)/E[score], and a drift/tilt/persistence line; the Position card headline
  shows the `markov_adjusted_score` (with a `base … · Markov …` subtitle) — **the
  BUY/HOLD/SELL label is unchanged** (the tilt is advisory on the score). When `markov` is
  absent the card hides and the row falls back to Position + Investor (two equal cards). Pure
  builders (`markov_band_chip`/`markov_metric_rows`/`markov_drift_row`/
  `markov_forecast_figure`/`position_headline`) unit-tested.
- Design/plan: [design](docs/plans/2026-06-21-markov-trade-analyzer-design.md) /
  [plan](docs/plans/2026-06-21-markov-trade-analyzer.md).

**Autonomous Driver — Claude decision layer (`/driver`) — DONE (2026-06-24).**
The driver's hardcoded `trade_selector` rule tree is replaced by a strategy-agnostic
**Claude decision layer** (autonomy **level B** — autonomous **paper** execution, NO
approval gate) that pursues **net $500/day** by selecting + sizing **defined-risk
option credit spreads (PCS/CCS/IC)** from the scanner. *Honest framing:* it **targets**
$500/day (presses when edge exists, **stands down** when it doesn't, **banks** the day
at +$500, hard-capped on the downside) — no decision-maker can guarantee it. Pieces
(all `services/driver_svc`):
- **PURE safety core** `guardrails.py` (the load-bearing module — the model PROPOSES,
  this code DECIDES, it never trusts the model with risk): `is_allowed` (defined-risk
  allowlist PCS/CCS/IC, structure read from `structure`→`type`→`trade_type` so the RAW
  scanner signal classifies correctly — real signals store it in `type`),
  `clamp_quantity` (resize to `min(request, per-trade cap, daily-budget cap)`, floored;
  0 on unaffordable / NaN / inf), `halt_state` (banked-$500 → daily-loss-cap → VIX>25),
  `apply_guardrails` (halt → stand-down → per-trade resolve-from-menu / allowlist /
  clamp / max-trades+concurrent, tracking the remaining budget across trades).
  Exhaustively unit-tested.
- **Decider** `decider.py`: `build_packet`'s model-facing prompt + a forced
  `submit_decision` tool-use call to **Claude Opus 4.8** (`anthropic` SDK, LAZY import,
  key via `api_keys.anthropic_api_key()` — env / gitignored `shared/anthropic_key.txt`);
  `parse_decision` is total over adversarial JSON and **every failure → stand-down**
  (never raises, never trades blind).
- **Compute**: `build_packet` (top-N composite-scored menu + day-P&L gap-to-target +
  `menu_by_id`→raw signal; day-P&L from the paper snapshot's `session_pnl`; real
  scanner keys `type`/`expiration`/`pop_pct`) + `run_cycle` (`build_packet → decide →
  apply_guardrails`, never raises) + `fetch_market_context`.
- **Handlers**: `run_autonomous_cycle` (gate on control → run_cycle → enqueue
  `cmd:options` `paper_create` per survivor [a `source="driver"` COPY + the CLAMPED
  qty; each enqueue isolated so a mid-loop failure can't skip the latch/publish] →
  latch the kill-switch on halt → publish `AutonomousState`); control read/write;
  `cycle`/`enable`/`disable`/`stop` commands.
- **Scheduler**: `checkpoint_due` (autonomous **entry window 09:45–15:30 ET**, 30-min slots — open's first ~15 min skipped + no new entries in the last 30 min; tuned 2026-06-29 to match the daily playbook; **trading days only** — weekend + NYSE-holiday gated via the service's own `_HOLIDAYS` (2026-07-05), so no Claude call fires on a market holiday) + `should_rearm`
  (next-day halt clear) wired into the loop on the executor, each branch guarded; the
  legacy 09:28 `morning_due`→`run_morning` path coexists (autonomy is gated OFF by
  default so they don't conflict).
- **Contracts** `DriverControl` (`cache:driver:control` — master switch + STOP latch)
  + `AutonomousState` (`cache:driver:autonomous` — the monitor view). Tunables in
  `settings.py` (`DAILY_TARGET=500`, `PER_TRADE_MAX_RISK=3000`, `DAILY_RISK_BUDGET=12000`,
  `MAX_CONCURRENT=10`, `MAX_TRADES_PER_CYCLE=5`, `VIX_MAX=35`, `MENU_TOP_N=15`,
  `DAILY_LOSS_HALT=1500` — the **"Very Aggressive" risk profile (2026-07-02, user choice)**:
  the driver presses toward $500/day and tolerates real drawdown (~half the $25k paper book
  deployable, ~12%/trade, a $1,500 daily-loss stop = 3× the target). **All risk knobs now live
  in `settings.py`**; `compute._daily_max_loss` reads `DAILY_LOSS_HALT` first (legacy
  `config.RISK_LIMITS` is only a fallback — this replaced the old $250 halt that stopped the
  day after one losing $SPX). The guardrail evaluates affordability in per-contract dollars
  (`guardrails.CONTRACT_MULTIPLIER=100` — the scanner's `max_loss` is PER-SHARE) and the paper
  open path uses its own matching `options_svc.compute._DRIVER_MAX_RISK_PER_TRADE=3000` so the
  user's MANUAL account stays at `config_paper.MAX_RISK_PER_TRADE=250`. The `decider._SYSTEM`
  prompt is an AGGRESSIVE mandate (take reasonably-scored trades to build toward the target;
  stand down only on genuinely poor edge / hostile conditions). `MODEL="claude-opus-4-8"` (build
  default; the **`DRIVER_MODEL`** env var / gitignored `shared/driver_model.txt` override it
  per-deployment — e.g. `claude-sonnet-5`), `CHECKPOINT_MIN=30`).
- **Page** `webgui/pages/driver.py`: a Tier-1 **monitor + override** (Enable/Disable,
  confirm-gated **STOP**, **Run now**, $500 progress, open-driver-positions, newest-
  first decision-log audit) reading `cache:driver:autonomous`/`control` + version-
  polling; engine-free (3-tier rule). The legacy approval queue + Performance UI stays.
**Real `/ES` `/MES` FOP shelved** — Schwab can't serve FOP chains or place futures/FOP
orders (equity+option only); see [[schwab-api-instrument-limits]]. v1 executable
universe is scoped to scanner spreads (equities + Claude-managed exits + live/level-C
are v2 — see the design doc). **Master switch defaults OFF** — set `ANTHROPIC_API_KEY`
and Enable on `/driver` to run it; with no key it safely stands down. driver_svc **130**
+ contracts **34** + webgui **483** green (incl. a Redis-driven e2e proving qty=3
clamps to 1 through the real pipeline; built subagent-by-subagent w/ per-unit TDD +
spec/quality review). Design/plan:
[design](docs/plans/2026-06-24-driver-autonomous-claude-decider-design.md) /
[plan](docs/plans/2026-06-24-driver-autonomous-claude-decider-plan.md).

**Driver isolated paper account + performance scorecard (`/driver`) — DONE
(2026-06-25).** The autonomous Driver now trades into — and grades itself against — its
**own dedicated paper book**, isolated from the user's manual paper account, so its real
performance is measurable. This also fixed a latent **write/read split**: the Driver
*wrote* `paper_create` into the flat LEDGER (`trades.db` — no repricing/auto-manage, so
its trades were inert rows and its `source="driver"` tag was dropped) but *read* its
day-P&L/$500-target/halt from the user's ENGINE account (`paper_account.db`) — measuring
the wrong book and never repricing its own trades. Pieces:
- **Dedicated account.** `repo_paths.DRIVER_PAPER_DB =
  options-scanner/data/paper_account_driver.db` ($25k start). Every `paper_account_db`/
  `paper_engine` fn already takes a `db_path`, so a second DB file is a fully independent
  single-account store — **zero schema change** (the `CHECK(id=1)` single-account
  constraint is sidestepped by using a separate file).
- **`services/options_svc` (owns ALL `paper_engine` imports):**
  `compute.open_driver_position(signal, qty)` (extracted from `run_entry_cycle`'s
  per-signal block — simulated fill → re-size on the ACTUAL fill credit → reserve BP →
  `paper_engine._record_order` (preserves the `entry_order_id` link) → `insert_position`;
  the guardrail qty is a **CEILING**, `open_qty = min(clamped, sized-on-fill)`; never
  raises); `run_driver_manage_cycle()` (`paper_engine.run_manage_cycle(db_path=
  DRIVER_PAPER_DB)` — reprice + auto-exit + session roll + halt, try/except never-raise);
  `driver_account_view()` / `driver_account_perf()`; the PURE
  **`driver_perf.build_scorecard(positions, snapshot)`** (# trades, open/closed, **win
  rate**, **profit factor** [None when no losses yet → render "—"], avg win/loss,
  realized/unrealized/total P&L, best/worst [drawn from the None-pnl-excluded set],
  **P&L by symbol & by strategy**). Handlers: `refresh_driver_paper` publishes **both**
  views (**NO rescue overlay** — that reads the manual book) + `run_driver_manage_and_refresh`;
  commands `driver_paper_create` / `driver_paper_manage` / `driver_paper_reset`.
  Scheduler: the 5-min `manage_due` slot reprices the driver account in its **OWN guarded
  branch** so a driver-side failure can't skip the manual refresh.
- **`services/driver_svc` (engine-free re the paper account — only enqueues + reads
  cache; it must NOT import `paper_engine`/`paper_account_db`, which transitively pull
  `scoring`/`signal_repricer` → the documented cross-app module collision):**
  `run_autonomous_cycle` enqueues **`driver_paper_create`** (not `paper_create`), reads
  day-P&L + open positions from `cache:options:driver_paper_account`
  (`CACHE_OPT_DRIVER_PAPER`), and attaches the scorecard (`cache:options:driver_paper_perf`)
  to the published **`AutonomousState.perf`** (new additive field). `build_packet`
  open-position attribution is correct-by-construction (the whole driver DB is the
  driver's — the dead `source=="driver"` filter falls back to the full account).
- **Cache views:** `cache:options:driver_paper_account` (snapshot + open positions) +
  `cache:options:driver_paper_perf` (the scorecard) — published on each
  `driver_paper_create` and every 5-min manage tick.
- **Page** `webgui/pages/driver.py`: the monitor's Day-P&L bar / summary / open positions
  **re-point** to `cache:options:driver_paper_account` (was the manual `paper_account`); a
  new **Performance scorecard card** (pure builders `scorecard_headline_chips` /
  `scorecard_quality_chips` / `scorecard_symbol_rows` / `scorecard_strategy_rows` /
  `best_worst_text`) renders `cache:options:driver_paper_perf` directly (live — refreshes
  on the 5-min tick, not just the 30-min cycle). Engine-free (3-tier rule).
- **PAPER ONLY** — `config.PAPER_TRADE` stays True; the driver never flips it. The
  historical ledger MU trades are left where they are (the driver starts fresh in its
  dedicated account). options_svc **285** + driver_svc **138** + contracts **35** +
  webgui **510** green (incl. a Redis-driven e2e proving `driver_paper_create` lands ONLY
  in the driver DB — manual account untouched — and both views + the scorecard reflect it,
  with a non-vacuity leak check). Built subagent-by-subagent (TDD, two-stage spec+quality
  review per unit). Branch `Using_Highcharts`. Design/plan:
  [design](docs/plans/2026-06-25-driver-isolated-paper-account-design.md) /
  [plan](docs/plans/2026-06-25-driver-isolated-paper-account-plan.md).

**Driver page (`/driver`) — DONE (2026-06-16, born 3-tier — Phase 5).** The
order-approval queue was built directly on the 3-tier model. New service
`services/driver_svc` (:8214, `SERVICE_PORTS["driver"]`), **scheduled (09:28 ET)
+ command-driven**. The order-approval queue is a Redis Streams flow: the morning
pipeline produces a *pending* approval cached at `cache:driver:approvals`; the
GUI APPROVE/SKIP buttons enqueue `cmd:driver` commands the consumer acts on.
Pieces:
- **Contracts** `shared/contracts/driver.py`: `ApprovalState` (the
  pending/decided morning payload — grade, grade_reasons, conditions, pnl, a
  loose `proposed_trades: list[dict]`, status pending/no_trade/error/approved/
  skipped, decision, results, reasons, error) + `PerfReport` (summary + trades).
  Validate the envelope shape before caching, like `ScanResult`/`TradeAnalysis`.
- **`driver_svc/compute`** ports the legacy `morning_agent.run_morning_agent()`
  orchestration **minus** its side effects (no `pending_trade.json` write, no
  HTTP post to the :8300 approval server): `run_morning()` calls the SAME
  building blocks (`check_service_health`/`fetch_all_ml_signals`/
  `fetch_gex_snapshot`/`fetch_market_conditions`/`fetch_current_pnl`/`grade_day`
  → `trade_selector.select_trades`) and **returns** the payload; `execute()` →
  `order_executor.execute_trades` (**`config.PAPER_TRADE=True` → simulated**, not
  modified); `build_perf_report()` → `perf_report.build_report`. All **defensive**
  (degrade to an `error`/empty payload, never raise). claude-driver engines are
  imported **standalone** (its dir on `sys.path`) — safe because the service is
  its own process (same isolation `sentiment_svc`/`trade_svc` use; note: importing
  both `driver_svc` and `trade_svc` engines in **one** process re-triggers the
  documented `config` module-name collision, so run service test suites **per
  folder**, never `pytest services` over all of them).
- **`driver_svc/handlers`**: `run`→cache pending approval; `approve`→**only if
  still pending**, `execute` the proposed trades + re-cache as `approved` w/
  results; `skip`→mark skipped; `perf`→cache `cache:driver:performance`. Each
  validates + caches + publishes an event. **`driver_svc/scheduler`**:
  `morning_due(now, last_run_date)` fires `run_morning` once/day at/after 09:28 ET
  on weekdays (holiday short-circuit lives in `compute.run_morning`) + keeps the
  perf view warm; **the scheduler NEVER executes orders** (only an explicit
  `approve` does). **`driver_svc/app`** = `make_app("driver", scheduler=loop,
  command_handler=…)`.
- **Page** `webgui/pages/driver.py`: Run-morning-agent + Refresh-performance
  buttons; an approval card (grade chip, conditions strip, grade rationale,
  per-bucket proposed-trade cards) with **APPROVE (confirm dialog)** / **SKIP**
  when pending, else a decision banner (approved/skipped/no_trade/error); a
  Performance section (summary line + trade table). Version-polls
  `driver:approvals` + `driver:performance`; persists across nav. Pure builders
  (`grade_color`/`status_text`/`condition_rows`/`proposed_trade_lines`/`perf_*`)
  unit-tested in `webgui/tests/test_driver.py`.
- Tests: `services/driver_svc/tests` (compute/handlers/scheduler/app) +
  `webgui/tests/test_driver.py`. Design/plan: Phase 5 of the
  [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**Portfolio page (`/portfolio`) — DONE (2026-06-16, born 3-tier — Phase 3).** The
stub Portfolio page was built directly on the 3-tier model. New service
`services/portfolio_svc` (:8212, `SERVICE_PORTS["portfolio"]`), **scheduled +
command-driven** — uniquely it keeps a **live model in memory** that a background
SSE consumer updates tick-by-tick. Pieces:
- **Contract** `shared/contracts/portfolio.py:PortfolioModel` (one view,
  `cache:portfolio:positions`): display-ready `holdings_rows` / `sector_rows` /
  `performance_rows` (already formatted by the engine `view_model` in Tier 2),
  the per-symbol `suggestions` map, and `proxy_up`/`streaming` meta. Validates the
  envelope shape before caching, like the other domain contracts.
- **`portfolio_svc/compute`** reuses `portfolio-analyzer/src` **verbatim**:
  `build_portfolio` (sector breakdown + the four comparisons: vs-sector RS,
  benchmark over/under-weight, since-purchase excess, tailwind), `compute_baseline`
  (slow per-EQUITY history stats — ports the desktop `_compute_baselines` worker),
  `evaluate_portfolio` + `suggest` (the live scorecard + advisory rules), and the
  app's `view_model` formatters. **Formatting lives in Tier 2** (`format_payload`)
  so the GUI stays a thin renderer. `src` is imported **standalone** (PORTFOLIO_ANALYZER
  on `sys.path`) — safe because the service is its own process (note: `portfolio-analyzer`
  AND `trade-analyzer` BOTH expose a top-level `src` package, so they collide if
  imported in one process — another reason to run service suites **per folder**).
  All functions defensive (degrade, never raise).
- **`portfolio_svc/state`** holds the in-memory `PortfolioState` singleton (raw
  model + baselines + `dirty`/`rebuild_requested` flags + a lock) shared by the
  scheduler thread and the command handler.
- **`portfolio_svc/handlers`**: `rebuild` (proxy health → daily trade sync →
  `build_portfolio` → baselines into state → publish), `publish_current` (re-format
  the current state + cache+publish — called on each throttled tick), `handle_command`
  (`refresh` → sets `rebuild_requested` for the scheduler, which owns the stream
  restart). **`portfolio_svc/scheduler`**: builds the model, runs a background SSE
  worker (`compute.make_data().stream_quotes` → `apply_tick` to the shared model),
  and a 2 s publish loop that republishes when ticks are pending + does a full
  rebuild every ~10 min or on a pending refresh (restarting the stream on fresh
  holdings). Pure `rebuild_due`/`apply_tick_to_state` are unit-tested. **`portfolio_svc/app`**
  = `make_app("portfolio", scheduler=loop, command_handler=…)`.
- **Page** `webgui/pages/portfolio.py`: Refresh button + proxy/stream status bar;
  **Holdings / Sectors / Performance** tabs (`ui.table` rendering the cached display
  rows directly); the Performance tab adds a suggestion detail pane (row click →
  full advisory reasons). Version-polls `portfolio:positions` (live P&L via the
  service's per-tick republish); persists across nav. Pure builders
  (`proxy_status`/`stream_status`/`suggestion_text`/`status_line` + column defs)
  unit-tested in `webgui/tests/test_portfolio.py`. (Removed the now-orphaned
  `main.py:_stub` — Portfolio was the last stub page.)
- Tests: `services/portfolio_svc/tests` (compute/handlers/scheduler/app, 20) +
  `webgui/tests/test_portfolio.py` (8). Design/plan: Phase 3 of the
  [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**EOD Report redesign — DONE (2026-06-27).** The summary/detail were rebuilt around
**Daily / Weekly(WTD) / MTD performance, per book** (manual paper ledger + Driver
account, shown separately), plus **trade-type breakdowns** (by strategy / 0-DTE-Swing /
status) and **TOC + collapsible `<details>` navigation** (no JS — works in-app AND in
the exported standalone files). New pure builders in `webgui/pages/eod.py`:
`normalize_trades(raw, *, kind)` (one uniform `{symbol, strategy, trade_type, status,
entry_date, exit_date, realized_pnl, credit}` shape — the ledger keys `entry_time`/
`exit_time`/`entry_credit_total`/`trade_type`, the driver positions key `entry_ts`/
`exit_ts`/`entry_credit`+`quantity` and carry **no** `trade_type`); `period_buckets`
(realized/closed by **exit** date, opened/credit by **entry** date, week-to-date =
Monday→today, month-to-date = 1st→today); `breakdown_rows(trades, key)`;
`performance_table_html` / `breakdown_table_html` / `toc` / `details_section` /
`_book_now_line`. The summary keeps its activity tiles, adds a per-book performance
block; the detail adds the breakdowns + reuses the existing section builders inside
`<details>`. **One additive service change**: `compute.driver_account_view()` now also
returns `closed_positions` (the open-only view couldn't date-bucket the driver's closed
trades) — requires an `options_svc` restart + a republish (`driver_paper_manage`) to
appear. **Realized reads `$0`/`—` until trades close** — correct by design, not a bug
(both books currently have only open positions). webgui **539** + options_svc green;
verified live (summary perf tables, detail breakdowns PCS 19/CCS 9 + SWING 21/0-DTE 7,
`<details>` collapse, exported files). Design/plan:
[design](docs/plans/2026-06-27-eod-report-redesign-design.md) /
[plan](docs/plans/2026-06-27-eod-report-redesign-plan.md).

**EOD Report page (`/eod` + `/eod/detail`) — DONE (2026-06-18; redesigned 2026-06-27,
see above).** A **pure-webgui**
end-of-day report — no new service/port. It reads the caches the existing services
already publish (`options:scan` / `options:captured` / `options:paper_trades` /
`options:paper_account` + `driver:approvals` / `driver:performance`
+ `options:driver_paper_account` / `options:driver_paper_perf`) and rolls them
into a **Summary** and a **Detailed** report. Scope is
**Options activity + Driver** only (portfolio/sentiment intentionally excluded).
Built entirely in `webgui/pages/eod.py` — honors the 3-tier rule (webgui imports
only `nicegui` + `shared.bus` + `shared.contracts`). Pieces:
- **Single-source body.** Pure builders produce one HTML **fragment** + a scoped
  `EOD_CSS` string (mirrors the `gamma.py` Explain pattern — `ui.html` strips
  `<style>`, so CSS goes through `ui.add_css` in-app and is inlined into the file
  on export). `summary_fragment(snap, detail_href)` / `detail_fragment(snap)` +
  per-section builders (`captured_section` / `paper_section` / `scanner_section` /
  `driver_section`) are all **defensive** (missing/empty cache → a "No data" note,
  never raises) and unit-tested in `webgui/tests/test_eod.py` (16).
- **Generate + archive.** The **Generate** button calls `generate()` →
  `read_snapshot()` (snapshots the live caches) → `wrap_document(...)` wraps the
  same fragment+CSS into standalone `<html>` docs → `write_archive(...)` writes
  `summary.html` + `detail.html` into `webgui/data/eod/<CT-date>/` (gitignored, like
  the rest of `webgui/data/`; same date overwrites). The summary page lists past
  archived dates (`archive_dates`, newest first). The **in-file** summary→detail
  link is the relative `detail.html`; the **in-app** link is the route `/eod/detail`
  (the fragment takes the link target as a parameter — the only in-app/file diff).
- **File serving.** `main.py` adds `@app.get("/eod/file")` (mirrors
  `/options/explain`): returns an archived file as a raw `HTMLResponse` so its own
  `<style>` applies. The page's "Open summary/detail file" buttons + archive links
  open it in a new tab.
- **Wiring.** `("/eod", "EOD Report", "summarize")` in `FLAT_NAV`; `@ui.page("/eod")`
  → `eod.render()` and `@ui.page("/eod/detail")` → `eod.render_detail()` (both active
  `/eod` so the nav item highlights on detail too); `/eod` + `/eod/detail` added to
  `test_shell.py`. Design/plan:
  [design](docs/plans/2026-06-18-eod-report-design.md) /
  [plan](docs/plans/2026-06-18-eod-report-plan.md).

**Expected Move page (`/options/expected-move`) — DONE (2026-06-20).** A standalone
Tier-3 page that charts a symbol's recent price action plus a forward expected-move
cone for a given option strike/expiration. Reached via a **new-browser-tab handoff**
button on Scanner, Paper Trades, Captured Signals, and Calculator (or standalone from
the Options nav with manual symbol+expiry). Pieces:
- **Compute (Tier 2, `services/options_svc/compute.py`):** `compute_expected_move(symbol,
  expiry, legs, lookback="auto")` fetches a **DTE-aware** trailing-history window
  (`em_lookback_spec` → `_fetch_em_candles`: auto ≈ **3× DTE** trading days clamped to
  [20, 252], short DTE ≤2 → intraday 30-min; or a fixed `1mo`/`3mo`/`6mo`/`1y` override —
  replaces the old fixed `_EM_HISTORY_BARS=130`, partial bars skipped), the option chain,
  and spot (live quote else last close), then derives ATM IV for the expiry
  (`atm_iv_from_chain` — nearest-strike `volatility`, percent→decimal, exact-then-nearest
  fallback) and the cone (`em_cone`: one point/day, `width(t)=spot·atm_iv·√(t/365)`,
  anchored at spot). Fully defensive — returns a JSON-safe dict with `error` set on any
  failure (candles still drawn even when IV is unavailable). On-demand only.
- **Command/cache (`handlers.py`):** the `expected_move` command → `cache:options:expected_move`
  + `events:options:expected_move` (one latest-result view, like `calc_result`/`sim_result`).
- **Page (`webgui/pages/options/expected_move.py`):** engine-free reader — enqueues the
  command and version-polls the view. Pure builders `expected_move_figure` (Highcharts
  **candlestick** via `extras=["stock"]` + Upper/Lower EM dashed line series + datetime
  xAxis + x/y `crosshair.label` boxes) and `leg_lines` (yAxis plotLines: short solid /
  long dashed, put-red/call-blue) are unit-tested. One persistent `ui.highchart` built at
  render (ESM-import-map gotcha), updated in place; `@guard` on handlers. A **Look-back**
  dropdown (`em_lookback_options`: Auto≈3×DTE / 1mo / 3mo / 6mo / 1y) re-runs the last
  query with the chosen window; the active spec label shows in the status line.
  **No blank non-trading-day gaps:** the chart renders via `ui.highchart(...,
  type="stockChart")` so the x-axis is **ordinal** (`xAxis.ordinal:True`) — weekend/
  holiday gaps in the historical candles collapse automatically — and the forward
  cone (`em_cone(..., holidays=scheduler._HOLIDAYS, trading_days_only=True)`) omits
  weekend/holiday points so it lines up contiguously with the candles.
- **Handoff (`handoff.py`):** `signal_to_em_payload(signal)` normalizes a scanner/captured/
  paper signal dict → `{symbol, expiry, legs}` (per-type strikes via `_EM_LEG_FIELDS`);
  `send_to_expected_move` stashes (`_pending["expected_move"]`) + opens the page in a new
  tab. Scanner/Swing keep the shared 3-button `add_row_actions`; Paper/Captured use the
  Expected-Move-only `add_expected_move_action` (their rows map via `synth_from_trade`/
  `synth_from_captured`, which expose `strategy`→`type`); Calculator builds the payload
  from its `leg_inputs`. Design/plan:
  [design](docs/plans/2026-06-20-expected-move-page-design.md) /
  [plan](docs/plans/2026-06-20-expected-move-page.md).

**Simulator Replay tab (`/options/simulator`) — DONE (2026-06-20).** The third
legacy Tk simulator tab (`Replay`, alongside the already-migrated What-if /
IV-shock) was migrated to the 3-tier model. The in-process `ChainSnapshot` that
`sim_fetch` already stashes (and which carries `price_history`) is re-priced
along the underlying's recent path by the existing pure
`options_simulator.ReplayEngine`. Pieces:
- **Compute (Tier 2, `services/options_svc/compute.py`):** `sim_replay(symbol,
  expiry, kind, strike, direction, lookback="auto")` runs `ReplayEngine.full_trace`
  via `aggregate_position`, then ports the legacy window's **gap-compression /
  session** layout (overnight/weekend breaks collapsed onto a consecutive integer
  x-axis; `gaps`/`sessions`/`ticks`/`resolution`) into a **JSON-safe** dict
  (`x`/`prices`/`greeks{delta,gamma,theta,vega,rho}`/`timestamps`/`lookback`). The
  re-priced path is a **DTE-aware** window fetched here (`replay_lookback_spec` →
  `_fetch_replay_history` via the proxy: 0-DTE → 1-min/1d · ≤5 → 5-min/3d · ≤15 →
  5-min/5d · >15 → daily/~½×DTE; or a fixed override key), **NOT** the snapshot's
  fixed 2-day history — the expiry/DTE is only known at replay time. Defensive:
  `{}` on missing snapshot/contract, `{"error": …}` on IV≤0 / no price history.
  It is a **separate command/cache view** from `sim_run` (replay depends only on
  the contract selector + look-back, NOT the dt/mult sliders — so slider drags
  stay cheap).
- **Command/cache (`handlers.py`):** the `sim_replay` command →
  `cache:options:sim_replay` + `events:options:sim_replay`.
- **Page (`webgui/pages/options/simulator.py`):** a third **Replay** tab (now the
  default). Pure builder `replay_figure(trace, cursor)` draws ONE Highcharts
  element with **6 stacked yAxes** (Price + 5 Greeks) over the integer x-axis;
  session boundaries are dashed xAxis plotLines and the **scrub slider** is a
  client-side cursor plotLine (no command — same idiom as the ΔS overlay). The
  x-axis stays NUMERIC (dates in the tooltip / readout) to sidestep the datetime
  crosshair epoch-ms gotcha; the hover tooltip is capped at **2 decimals**
  (`tooltip.valueDecimals`). A **Look-back** dropdown (`lookback_options`: Auto-by-DTE
  / 1-min·1d / 5-min·3d / 5-min·5d / 15-min·10d / Daily·20d) overrides the DTE-driven
  window; the active spec label shows in the cursor readout. Built once at render
  (ESM import-map gotcha), updated in place; version-polls `options:sim_replay`;
  enqueues only on contract-selector / look-back changes. Pure builders unit-tested;
  verified live end-to-end (SPY → 62-bar 1-min trace → rendered 6-panel stack). Plans:
  [replay](docs/plans/2026-06-20-simulator-replay-tab-plan.md) /
  [DTE look-back](docs/plans/2026-06-20-dte-aware-lookback-plan.md).

**System Status page (`/status`) — DONE (2026-06-19).** A **pure-webgui** at-a-glance
health board (no new service/port). It honors the 3-tier import rule — `webgui/pages/status.py`
imports only `nicegui` + `bus_client`/`proxy` + `repo_paths`. Pieces:
- **Component sweep.** `component_targets()` enumerates the components from
  `repo_paths` (Memurai :6379, schwab-proxy :8100, **Schwab Authorization** (the
  proxy's OAuth token state), the five services :8210–8214 via `SERVICE_URLS`,
  webgui itself). `_probe_one` checks each by `kind`: **memurai** →
  `bus_client.ping()` (new helper: `bus()._r.ping()`, never raises); **proxy** →
  `proxy.health()`; **auth** → `auth_status(proxy.health())` reads `has_token`/
  `token_expired`/`refresh_token_expired` (access-token-expired is still "authorized"
  since the proxy auto-refreshes; only a missing token or **expired refresh token**
  is red); **service** → HTTP `GET /health` (the `make_app` scaffold's probe,
  `{"up": True}`); **self** → always up. `_sweep` fetches `proxy.health()` **once**
  and shares it with both the proxy + auth cards. The whole sweep runs off-thread
  via `nicegui.run.io_bound` (short 2.5 s per-probe timeout so a dead component fails
  fast). `overall_status` rolls the results into a green/red/grey banner naming any
  down components.
- **Schwab Authorization card (2026-06-19).** Surfaces OAuth token validity
  separately from "is the proxy process up". When the proxy is reachable the card
  shows an **Authorize** / **Re-authorize** button (`AUTH_URL = {PROXY_URL}/auth`)
  that opens the proxy's OAuth re-login page (`GET /auth`) in a new tab via
  `ui.navigate.to(..., new_tab=True)`; when the proxy is down the card is grey
  ("can't check"). The card is **not** restartable (`restart_spec` → None) — its
  action is the login link, not a process relaunch.
- **Data-freshness table.** Below the cards, per-domain rows read each representative
  cache view's version + ts (new `bus_client.read_meta(view)` → `(version, ts)`) and
  show `age_text` + a STALE flag for **scheduled** views older than 600 s (`is_stale`);
  **on-demand** views (trade/driver) are never flagged. This distinguishes "service
  answers /health" from "service is actively publishing".
- **Per-component Restart (2026-06-19; every card 2026-07-10).** **Every**
  component card carries a **Restart** button — shown regardless of up/down state,
  so you can also restart a wedged-but-listening service — covering the proxy, all
  **six** Tier-2 services (sentiment/options/portfolio/trade/driver/market), Memurai,
  and **the webgui itself**. Only the **auth** card is excepted (its action is
  **Authorize**, a link to `/auth`, not a process restart). `restart_spec(target)`
  maps a component to how it restarts — a **script** spec (proxy / service / **self**:
  free the port then launch the venv python on the entry script; services pass
  `wait_port=8100` so they wait for the proxy, the webgui uses `wait_port=0`) or a
  **service** spec (Memurai → `Restart-Service`, falling back to `Start-Service` if
  stopped — works up or down; may need an elevated session). **Windowless (2026-07-10):**
  `restart_command(spec)` builds a `cmd /c tools\restart_one.bat <kill_port>
  <wait_port> <name> <script>` argv and `_do_restart` spawns it with
  **`CREATE_NO_WINDOW`** — nothing flashes. `restart_one.bat` taskkills the port's
  LISTENING owner (`/F /PID`, no `/T` — so the webgui's own self-restart doesn't take
  the spawn down with it), waits for the dependency (`ping`-based sleep, no console
  needed), then launches the component **hidden** via `pythonw` +
  `Start-Process -WindowStyle Hidden` with stdout/stderr → `logs\<name>.out.log` /
  `.err.log` (mirrors `start_all_wt.bat nowindow`). The **webgui self-restart** frees
  :8500 and relaunches even though it kills the current page — the click handler toasts
  "this page will disconnect; reload" and skips the re-sweep. Every other restart
  toasts + schedules a 7s re-sweep. Verified: a live proxy restart (prior turn) bound
  :8100 in ~1s; the windowless `restart_one.bat` launch primitive is smoke-tested
  (hidden `pythonw`, output captured to `logs\`).
- **Wiring.** `("/status", "System Status", "monitor_heart")` in the **More** nav
  group; `@ui.page("/status")` → `status.render()`; `/status` added to
  `test_shell.py`. Auto-refresh `ui.timer(15s)` + manual Refresh button (with
  spinner + re-entrancy guard). Pure builders (`component_targets`/`status_word`/
  `status_color`/`status_icon`/`overall_status`/`age_text`/`is_stale`/`freshness_row`
  + `restart_spec`/`restart_command` + `auth_status`) unit-tested in
  `webgui/tests/test_status.py` (35); render + live restart + live auth-card
  verified by screenshot.

**Rescue tested trades (`/options/rescue`) — DONE (2026-06-21).** An advisory +
one-click-apply rescue feature for **tested credit spreads** (PCS/CCS/IC). Architecture
is **"Approach C (hybrid)"**: cheap **at-risk detection** rides the existing 5-min
manage cycle (tags paper-account rows + publishes a summary for a nav badge), while the
expensive **ranked candidate menu** is computed **on-demand** via a command, and **apply**
executes through new paper-engine primitives behind a stale-price guard. Pieces:
- **Commission source of truth** `config/commissions.toml` — Schwab standard rates
  (options **$0.65/contract per leg**, futures $2.25/side, index-exchange-fee passthrough);
  loaded by `services/options_svc/commission.py` (`commission_for`/`futures_commission`/
  `is_index_symbol`). **Rule: don't hard-code rates** — add them here.
- **Contracts** `shared/contracts/options.py`: new `RescueAdvisory` + `RescueCandidate`
  (+ `RescueLeg`/`RescueMark`) — validate the advisory envelope before caching.
- **PURE engine** `services/options_svc/rescue.py`: `assess_position_risk` (ok/watch/
  tested/critical + 0-100 **heat**, thresholds mirror the manage-cycle stops),
  `strategic_context` (dealer-gamma/regime/settlement notes+flags), **11 candidate
  builders** (close, partial_close, narrow, convert_ic, convert_butterfly, broken_wing
  [advisory], roll_down, roll_out, roll_down_out, inverted [advisory], futures_hedge
  [advisory]), `score_candidate` (max-loss-reduction-per-net-$ + delta + credit-vs-debit
  penalty + GEX/regime modifiers), and the `rescue_candidates` orchestrator (ranks +
  attaches context/warnings; **per-item construction** so one bad candidate can't sink
  the advisory).
- **Compute (Tier 2)** `services/options_svc/compute.py`: `compute_rescue(position_id)`
  (loads the position, reprices via `signal_repricer.reprice_swing`, fills underlying from
  the `gamma_snapshot` spot when the live quote is missing off-hours, pulls regime from the
  sentiment bridge, runs the engine, returns a contract-validated dict — fully defensive),
  `assess_open_positions()` (cheap stored-marks pass for the badge), `_make_leg_pricer(symbol)`
  (per-expiry chain-mid pricer).
- **Handlers** `services/options_svc/handlers.py`: the manage-cycle overlay merges
  `rescue_state`/`heat` onto the paper-account view; `publish_rescue_summary`; `rescue` +
  `rescue_apply` command handlers (`rescue_apply` refuses non-paper/captured ids and **never
  mutates on a stale re-price**). Cache keys: `cache:options:rescue:<position_id>` (one
  per-position advisory) + `cache:options:rescue_summary` (n_tested + n_critical for the badge).
- **Apply primitives** `options-scanner/paper_adjust.py` (NEW): `apply_close`/
  `apply_partial_close`/`apply_narrow`/`apply_convert_ic`/`apply_convert_butterfly`/
  `apply_roll`/`apply_inverted` mutate the paper DB inside the existing cash/buying-power
  mechanism (reconciling reserved BP to the new max-loss), write an audit row, and the
  `apply_adjustment` dispatcher re-prices the candidate legs and **aborts without mutation**
  if economics drifted > tolerance or the position isn't OPEN. `options-scanner/
  paper_account_db.py` grows a `position_adjustments` audit table + a `parent_position_id`
  column on `paper_positions` (linked rolls) + `insert_adjustment`/`list_adjustments`.
- **Page** `webgui/pages/options/rescue.py` (Tier-1, engine-free): `render()` + pure builders
  (`heat_color`/`at_risk_rows`/`candidate_card_rows`/`cash_text`/`summary_line`) — an at-risk
  table (paper+captured, heat-colored) → select a position → enqueues `rescue` → version-polls
  `cache:options:rescue:<id>` → ranked candidate cards (execute cards Apply→confirm→
  `rescue_apply`; advisory cards show "manual"). One persistent `ui.highchart` (ESM-import-map
  gotcha); `@guard` on handlers; degrades to a waiting-for-service placeholder.
- **Wiring** `webgui/main.py`: `("/options/rescue", "Rescue", "healing")` in the Options nav
  group + `@ui.page("/options/rescue")` route + a red count badge (key `/options/rescue`) fed
  from `cache:options:rescue_summary`, cleared on page open; `/options/rescue` in
  `test_shell.py`; a `webgui/page_help.py` guide entry.
- **At-risk row highlights:** the `rescue_state`/`heat` overlay lands on
  `cache:options:paper_account`, so the heat-colored at-risk row tint is wired on the **Paper
  Portfolio** page (`/options/portfolio`, `pages/options/portfolio.py` `rescue_highlight` +
  `body-cell-symbol` slot) — live there. The earlier-wired tints in `paper.py` (paper_trades
  ledger) + `captured.py` render different views that don't carry the overlay, so they stay
  **dormant no-ops** (kept defensively; captured is forward-compatible if signals are ever
  flagged). Primary at-risk surfaces (Rescue page table + nav badge) work regardless.
- **Captured CUT signals as advisory candidates (2026-06-22).** A captured signal whose
  `recommendation` is **CUT** (a money/delta/time loss stop — not `TARGET_HIT`) is now an
  **advisory** rescue candidate. `compute.reprice_captured` tags each signal row with
  `rescue_state`/`heat` via `assess_position_risk`, **escalating a CUT to at least `tested`**
  (heat floored ≥60) so it lands on the rescue board; `compute_rescue(position_id, source=
  "captured")` loads the signal via `signal_db.get_signal`, runs the engine, and **forces every
  candidate `apply_kind="advisory"`** (a captured signal has no executable paper position — the
  cards show the roll/convert/close mechanics + economics for *manual* placement, no Apply). The
  `rescue` command carries an optional `source` arg (paper|captured; paper id coerced to int,
  captured passes the string `signal_id`); `RescueAdvisory` gained `source` + a `position_id:
  int | str`. The page (`at_risk_rows` keyed by `signal_id`, row-select passes `source`) surfaces
  them. Apply safety is enforced at three layers (forced-advisory cards · `rescue_apply` refuses
  non-paper ids · the apply branch int-coerces). The nav **badge stays paper-only** (captured
  CUTs show on the board, not the badge). Design: [captured-signals](docs/plans/2026-06-22-rescue-captured-signals-design.md).
- Design/plan: [design](docs/plans/2026-06-21-rescue-tested-trades-design.md) /
  [plan](docs/plans/2026-06-21-rescue-tested-trades-plan.md).

**Signal push notifications — Telegram / Discord / Google Fi SMS — DONE (2026-07-05).**
The always-on options service now pushes a **phone notification** the moment it
publishes a **new scanner signal** or a **new captured signal** — server-side, so the
phone is pinged 24/7 regardless of whether a browser tab is open (this deliberately does
NOT reuse the browser-gated webgui alert watcher). Self-contained, service-owned module
**`services/options_svc/push_notify.py`** (headless; ports the proven Telegram/Discord
formatters from the legacy `options-scanner/notifier.py` rather than importing it — that
module drags in `winsound`/`winotify` and `notifier` is a documented cross-app name
collision). Three channels, each **self-gating on config presence** (missing creds →
silent no-op): **Telegram** (Bot API, HTML, one msg per new signal), **Discord** (webhook
embed, one per new signal), and **SMS via Google Fi** (`smtplib` emails a **batched**
summary to `<10-digit-Fi-number>@msg.fi.google.com` — Fi's proprietary email-to-text
gateway, still functional in 2026 unlike the deprecated `@vtext`/`@tmomail` carrier
gateways — sent from Gmail over `smtp.gmail.com:587` STARTTLS with an **app password**).
**Triggers** are hooked at the existing publish points in `handlers.py`: `rescan` (new
scanner signals) and `refresh_captured` + the `captured_reprice` branch (new captured
signals; `remove_closed_from_captured` is deliberately NOT wired — a manual close is not a
new signal). Each hook is **best-effort + try/except-wrapped AFTER the cache_set/publish**
so a notify failure can never block the scan/publish path. **"New" detection** is
single-source + restart-safe: a stable signal key (symbol/type/strikes/expiration, IC
folds the call legs) diffed against a **date-scoped Redis seen-set**
(`cache:options:notified_scan` / `cache:options:notified_captured`, a `{date, keys[]}`
envelope that resets on a new trading date) — keys are marked seen **when diffed (before
gating)**, mirroring the webgui watcher's unconditional `alerted |= keys`, so each signal
is considered once; a signal first seen off-hours/disabled/below-min-score is absorbed and
not deferred. On the service's **first publish after (re)start** the set is seeded
**silently** (no re-notify storm). Gates: a master `enabled`, an optional `market_hours_only`
(a local weekday + 08:00–15:00 CT + holiday check copied byte-for-byte from
`webgui/alerts.py` to avoid importing NiceGUI into the service — update the `_HOLIDAYS`
copy yearly alongside `alerts._HOLIDAYS`), and a scanner-only `min_score` (captured signals
carry no `composite_score`). **Config**: gitignored `shared/notifications.json`
(+ committed `shared/notifications.example.json`; `repo_paths.NOTIFICATIONS_CONFIG`), env
vars override file values (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`DISCORD_WEBHOOK_URL`/
`FI_SMS_NUMBER`/`SMS_SMTP_USER`/`SMS_SMTP_APP_PASSWORD`/`NOTIFY_ENABLED`). **Setup**:
Telegram bot via `@BotFather` (token) + `.../getUpdates` (chat_id); Discord channel →
Integrations → Webhooks; SMS = your 10-digit Fi number + a Gmail **App Password** (Google
Account → Security → 2-Step Verification → App passwords). **Out of scope (YAGNI)**:
per-channel Settings-page toggles, and trade-executed/error notifications. push_notify
**27** + options_svc handlers **45** green. Built subagent-by-subagent (TDD, two-stage
spec+quality review). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-07-05-signal-push-notifications-design.md) /
[plan](docs/plans/2026-07-05-signal-push-notifications-plan.md).

**Twitter/X public-post channel + grade (2026-07-20).** A **fourth** channel on the SAME
`notify_signals` fan-out posts new SCANNER (0-DTE + swing) signals to a **public** X account.
Unlike the other three (which push to YOU), this PUBLISHES, so it is deliberately different:
`notify_twitter` is **scanner-only** with its OWN gates (a public `min_score` + a persisted
per-day `daily_cap`), `twitter_signal_text` is a **≤280-char** formatter (compact body + a
config-driven footer: hashtags/Discord-link/extra-text/disclaimer, footer preserved on
truncation), and `send_twitter` is a **tweepy OAuth 1.0a** sender (best-effort — 187/429/network
errors caught). It is wired into `notify_signals` guarded so a Twitter failure can't break the
private sends, and **ships OFF** (`twitter.enabled:false` + `dry_run:true`) — inert until OAuth
keys are added + both flags flipped (account creation + the go-live flip are the USER's; nothing
publishes by default). The signal **grade** was added to the tweet + `telegram_signal_text` +
`discord_signal_embed` at the same time. Config: the `twitter` block in `shared/notifications.json`
(+ `TWITTER_*` env). New dep `tweepy>=4.14`. **Restart `options_svc`.** push_notify **73** green.

---

## More feature notes migrated out of CLAUDE.md (2026-08-16)

From the "webgui structure" section: the app-wide alerts/badges build, the Market Dashboard
and Market Summary Ticker builds, the multi-strategy Swing Scanner Phase 1 build, and the
phase-by-phase (P0-P8) log of the completed Tailwind-first UI migration. Verbatim; the
standards those produced stay in CLAUDE.md.

**App-wide alerts + nav badges (DONE — 2026-06-17).** `_layout` mounts a hidden
`<audio>` + a `ui.timer(2s)` watcher (`alerts.py` pure helpers + `main._run_watcher`)
that runs on **every** page: it chimes a bundled WAV (`webgui/static/sounds/{chime,
bell,ping}.wav`, served at `/static`) — and optionally fires a desktop
`Notification` — on new qualifying scanner signals (gated by enable/market-hours/
min-score in `app_settings`; view-staleness alerts use PER-VIEW thresholds — `alerts.stale_after`/`STALE_OVERRIDES`, `options:scan` = 20 min so the 15-min autoscan isn't falsely flagged between scans), and maintains red count badges on **Scanner** (new
signal keys), **Captured Signals**, and **Driver** (pending approval) nav items
(`_NAV_BADGES`, single-user like `_NAV_OPEN`; cleared when you open that page).
GUI prefs persist via `webgui/app_settings.py` → `webgui/data/settings.json`
(**gitignored** since 2026-08-09; missing keys — and a missing file — regenerate
from `DEFAULTS`, so each checkout carries its own preferences. It was tracked
before that, which made `tools\promote.bat` refuse on a dirty tree every time
anyone changed a setting in prod's GUI; the file is runtime state the app writes,
so it can never be clean. Untracking it means a **pull deletes the existing copy**
in any checkout that had it — back it up and restore it across that one promote).
The **Settings** page (`/settings`,
`pages/settings.py`) binds the alert toggles/sound/volume/market-hours/min-score +
desktop-notification controls. The drawer is restyled (`.nav-drawer` CSS: active
pill, hover, right-aligned badges, title block). Browsers block autoplay until a
user gesture — clicking any nav link or **Test sound** unlocks it. Design/plan:
[design](docs/plans/2026-06-17-scanner-alerts-settings-badges-design.md) /
[plan](docs/plans/2026-06-17-scanner-alerts-settings-badges-plan.md).


**Market Dashboard (`/market`) — DONE (2026-07-07).** A new **More → Market Dashboard**
page streaming a live grid of ~48 macro tickers (from `symbol_categories.csv`), grouped
into a **framed panel per category** and colored by **semantic risk-on/off market
condition**. Sixth Tier-2 service. Pieces:
- **New service `services/market_svc` (:8215, read-only).** A scheduler polls the proxy's
  **raw `/quotes`** endpoint (not `SchwabProxyClient.get_quotes`, which discards
  `assetMainType`/`futurePercentChange`) for all real symbols in ONE batched call on a
  **~3 s RTH cadence** (`scheduler.poll_interval`, 15 s off-hours, 60 s deep-weekend when
  futures are closed — Sat all day / Sun before the 17:00 CT reopen — NOT throttled harder
  off-hours because the equity-index futures trade ~24h Sun-Fri and are the main off-hours
  mover; the shared `_HOLIDAYS` gate drives it. **Cadence tuned 2026-08-02 from 2 s/5 s →
  3 s/15 s** — the dashboard updates tiles in place so the slower poll is imperceptible, and
  it roughly halved this service's Schwab `/quotes` volume, ~24k → ~12k calls/day, the stack's
  #2 Schwab caller after GEX collection), normalizes change across INDEX/EQUITY/FUTURE,
  computes the `$ADVN-$DECN` breadth spread, reads the app's own cap-weighted put/call, derives a per-tile
  `color_state`, and publishes **`cache:market:dashboard`** (`skip_unchanged=True`, so no
  repaint on byte-identical ticks). No command handler — the page only reads.
- **Frame ordering (2026-08-05).** `symbols.CATEGORY_ORDER` sets the frame layout; **within** a
  frame, `symbols.SORTED_CATEGORIES` (Top 10 · Sector SPDR · Thematic / Industry ETF · Countries)
  marks the four **leaderboard** frames whose tiles `compute.rank_tiles` orders **descending by day
  %-move**. Three bands: composite **baskets pinned first** (BIG10 — its `change_pct` is the members'
  average, so value-sorting would bury it among its own constituents), then quoted tiles by
  `-change_pct`, then **no-data / value-only tiles last**; the sort is **stable**, so equal movers
  keep symbol-map order rather than jittering poll to poll. The remaining frames are ranked
  **deliberately not** — their curated order is meaningful (SPY/DIA/QQQ/IWM before the equal-weights;
  VIX before its tenors) and a test pins that a big QQQ move must not reshuffle the broad ETFs. The
  page applies the rank as a Tailwind flex `order-N` class rather than moving DOM nodes, preserving
  the build-once / update-in-place property (see the `/market` route-table entry).
- **PURE modules.** `symbols.py` = the **CSV→Schwab symbol map** (single source of truth):
  69 tiles (USO joined the Thematic / Industry ETF frame 2026-08-12) with per-symbol
  **polarity** (`normal` up=risk-on / `inverted` up=risk-off) +
  `kind` (`quote`/`spread`/`external`), encoding the translations (`SPX`→`$SPX`, `VIX`→
  `$VIX`, `SKEW`→`$SKEW`, `/ES[U26]`→`/ESU26`, ToS `IMGTN:CGI`→API **`$MGTN`** [CBOE
  Magnificent Ten Index]) and the **equivalents for symbols Schwab
  can't quote** (`$DXY`→**`UUP`**; `$PCALL`+`$PCSP`→one **"Put/Call"** tile
  fed from `cache:sentiment:composite` → `live.sector_pcr`). Two `external` tiles now share
  the **Options Sentiment** frame: **Put/Call** (`source="sentiment_pcr"`) and **Net Prem**
  (`source="options_net_prem"`, added 2026-07-21) — the dollar-weighted call/put premium skew
  fed from `cache:options:matrix`→`premium` via `compute.read_net_prem` (`build_dashboard`
  branches the external kind on `e["source"]`). `classify.py` = pure
  `normalize_quote` (asset-type-aware % field), `spread_value` (`$ADVN-$DECN` = leg last
  diff, colored by SIGN not magnitude since a count isn't a %), and `color_state`
  (polarity × sign × intensity → 6 buckets +
  `no_data`). `compute.build_dashboard` is PURE over an already-fetched raw dict + pcr; the
  `SYMBOL_MAP` whitelist iteration means the proxy's `errors` bucket can never become a
  bogus tile.
- **Coloring (design decision — semantic, not literal up/down).** Green = risk-on, red =
  risk-off, grey = flat/no-data, intensity by magnitude. **Inverted** instruments shade RED
  on up-moves: VIX/VIX1D/VIX3M, SKEW, the put/call tile, `UUP` (dollar strength), `TLT`
  (long-duration flight-to-safety). Defensive equity sectors (XLP/XLU/XLV) stay **literal**
  up=green (deliberate). Contract `shared/contracts/market.py:MarketDashboard`.
- **Page `webgui/pages/market.py` (Tier-1, engine-free).** Reads `cache:market:dashboard`,
  paints framed category panels (macro→tape→rotation frame order) of colored tiles
  (symbol + description tooltip + last + net/%-change), version-polls, and **updates tiles
  IN PLACE** (build-once + `.classes(remove=…, add=…)` bg swap keyed by the unique display —
  no per-tick DOM rebuild). Tailwind-first (data-driven colors from a finite `_BG` map, no
  `.style()`). Wired into `MORE_CHILDREN` + `/market` route; surfaced on `/status` (health
  board + freshness) and killed by `/terminate` (`stop_all.py` iterates `SERVICE_PORTS`).
- **"Streamed" caveat.** Schwab's SSE streamer is equities-only (indices/internals/VIX have
  NO streaming service; futures would need a proxy `LEVELONE_FUTURES` bridge), so ~half the
  symbols are REST-only regardless — the honest uniform path is the ~2 s poll (visually
  continuous). **Launch:** `start_all.bat`/`start_all_wt.bat` launch it as the 8th window/tab.
  **Restart `market_svc` (+ the webgui to pick up the new route)** to see it live.
  market_svc **30** + shared/contracts **43** + webgui **687** green; **live-verified
  end-to-end** (real proxy+Redis → all 47 tiles populated with correct semantic colors, incl.
  UUP/put-call equivalents; VIX+3.6%→risk_off_strong, TLT−1.1%→risk_on_strong,
  $ADVN-$DECN=−465→risk_off_mild). Built subagent-by-subagent (TDD, two-stage spec+quality
  review per layer). Design/plan:
  [design](docs/plans/2026-07-07-market-dashboard-design.md) /
  [plan](docs/plans/2026-07-07-market-dashboard-plan.md).

**Market Summary Ticker (every page) — DONE (2026-07-08).** A fixed scrolling marquee pinned
to the **bottom of every page** (rendered in `main.py` `_layout`) that gives an at-a-glance
market read synthesized from the app's own data. **Hybrid content:** it leads with a short
**Claude-written verdict** (the "why", refreshed on a schedule) then scrolls **live,
color-coded data items** (the fast numbers). Pieces:
- **Narrative — Claude, scheduled (`market_svc`).** `compute.build_summary_packet` (PURE)
  distills the dashboard + sentiment/trend caches into a compact packet;
  `compute.generate_summary` calls Claude (Sonnet 5, thinking disabled, `max_tokens≈220`,
  client built with `timeout=30/max_retries=1`) for a 1–2 sentence verdict; the scheduler's
  `summary_due` gate (~20 min RTH / ~60 min off-hours) publishes **`cache:market:summary`**
  (`MarketSummary` contract, `skip_unchanged=True`). Reuses the Gamma-Analyze pattern (lazy
  `anthropic`, key via `ANTHROPIC_API_KEY`→`shared/anthropic_key.txt`) — fully defensive:
  no key / API error → empty narrative → the ticker shows live items only. **Test hygiene:**
  a market_svc `conftest` autouse fixture monkeypatches `_make_summary_client→None` so the
  suite NEVER makes a live Claude call.
- **Live items — rule-based, Tier-1 (`webgui/pages/ticker.py`).** PURE `ticker_items(dashboard,
  sentiment)` composes `{text, tone}` items (sentiment score/bias, trend label/score, breadth,
  VIX/VIX1D/VIX3M/SKEW, put/call, SPX/NDX, top-4 sector/thematic movers by |Δ|); `item_class`
  maps `tone`→fixed Tailwind class (Tailwind-first, no `.style()`). Zero API cost, updates live.
- **Render.** `render_ticker(active)` (called in `_layout`, gated by the Settings toggle) is a
  fixed `ui.footer` marquee — the `@keyframes` animation is the ONE `ui.add_css` escape hatch;
  scroll speed is a **finite `speed_class`** (slow/med/fast), not an inline style. A `@guard`ed
  version-gated `ui.timer` reads the three cache versions and **only rebuilds when the RENDERED
  content signature changes** (not on every 2 s dashboard bump) → the marquee scrolls smoothly
  without tearing/jumping. Content column gets `pb-10` so the footer never covers content; the
  page-help now lives on the nav tabs + drawer items as 2 s-delayed hover tooltips (`main._help_tooltip`, 2026-07-12 — the header "?" fab is gone).
- **Control.** `app_settings` `ticker_enabled` (default on) + `ticker_speed`; a **Settings**
  page toggle (Show + Slow/Medium/Fast). When off, `render_ticker` renders nothing.
- **The toggle also gates the Claude call (2026-07-14).** `ticker_enabled` used to be
  Tier-1 only, so switching the ticker off merely hid the marquee while market_svc kept
  generating (and paying for) the verdict — it was the stack's **biggest Claude caller**
  (~21 of ~39 calls/day). The toggle now writes through: `settings.apply_ticker_enabled`
  enqueues `enable_summary`/`disable_summary` on **`cmd:market`** (market_svc's FIRST
  command handler — `handlers.handle_command`, wired in `app.py`) → `set_summary_enabled`
  records **`cache:market:summary_enabled`** → the scheduler reads it each cycle
  (`handlers.summary_enabled`) and feeds `summary_due(..., enabled=…)`, which
  short-circuits. **Defaults to enabled** on a missing key / unreadable bus (the flag can
  only turn the verdict OFF explicitly), and `secs_since` keeps accumulating while off so
  re-enabling yields a fresh verdict at once. Because a wiped Memurai drops the key (→
  back to enabled), `main.sync_ticker_setting` re-asserts settings.json at **webgui
  startup** — registered **inside the `__main__` guard**, NOT at module scope: pages
  `import main` lazily at request time and the entry script runs as `__main__`, so a
  module-level `app.on_startup` re-registers after NiceGUI started → `RuntimeError` → every
  page 500s (learned the hard way; pinned by `test_shell.py`'s reimport probe).
  **`SUMMARY_RTH_SEC` 20 → 40 min** the same day (the live items refresh on the 2 s poll,
  so a slower narrative costs the reader little). Steady state ~39 → ~18 calls/day.
- market_svc **35** + shared/contracts **38** + webgui **687** green (no live API calls in the
  suite); **live-verified** end-to-end (real Claude verdict published to `cache:market:summary`
  + 14 correct color-coded live items from the live caches). **Restart `market_svc` + the
  webgui** to see it. Built subagent-by-subagent (TDD, two-stage spec+quality review). Design/plan:
  [design](docs/plans/2026-07-08-market-summary-ticker-design.md) /
  [plan](docs/plans/2026-07-08-market-summary-ticker-plan.md).

**Multi-strategy Swing Scanner (`/options/swing`) — Phase 1 DONE (2026-06-30).** The
Swing Scanner was expanded from a credit-spread-only premium scanner to a **unified,
single-symbol multi-strategy scanner** that builds + ranks candidate structures across
strategy families on **one comparable 0–100 score**. The crux: the legacy `scoring.py`
9-factor model is a *premium-seller's* score (it punishes long calls/debit spreads —
negative theta, low PoP, undefined R:R, wants to avoid the expected move), so the heart
of this feature is a **new unified Fit+Quality scorer** that makes a long call and a
put-credit-spread comparable. Architecture = **two new PURE engine modules** (in
`options-scanner/`, process-isolated so no `scoring` collision) feeding the existing
options-service swing path. Pieces:
- **`options-scanner/strategy_scanner.py`** (PURE builders + payoff economics): emits a
  **normalized signal** for each candidate — a canonical `legs` list
  (`{kind,side,strike,expiration,qty,mark,delta,theta,vega,gamma,iv}`) + payoff
  economics computed off the structure. `payoff_metrics(legs, spot)` derives
  net_debit/credit, max_profit/loss, breakevens, capital, R:R, net greeks, and an
  **analytic `unbounded` flag** from the call-tail coefficient (`Σ sign·qty` over CALL
  legs: >0 → unbounded profit / <0 → unbounded loss / ==0 → bounded; the downside is
  always bounded at S=0) — bounded extrema read at payoff BREAKPOINTS (`{0} ∪ strikes ∪
  far-high`), NOT a spot-relative grid (so a short put's true `strike−credit` max loss
  is correct). `pop_from_payoff` = normal-terminal probability of the profit region.
  Builders: `build_directional` (LONG_CALL/LONG_PUT/SHORT_CALL/SHORT_PUT, delta-targeted)
  + `build_debit_verticals` (BULL_CALL/BEAR_PUT). `adapt_credit_spread`/`adapt_iron_condor`
  normalize the existing `screen_spreads` PCS/CCS + `build_iron_condors` IC dicts into the
  same shape (source economics stay authoritative; structural keys filled from the legs,
  with a source-derived breakeven fallback when leg marks are absent).
- **`options-scanner/strategy_scoring.py`** (PURE): `infer_market_view(technicals,
  iv_analysis)` → `{direction (bullish/bearish/neutral), conviction 0..1, vol_regime
  (low/mid/high)}` from the REAL upstream keys (`trend` UPPERCASE incl.
  RECOVERING→bullish/WEAKENING→bearish, `rsi14`, `sma20`/`price`; `iv_rank` PRIMARY,
  `current_iv`/`hv_current` IV/HV ratio FALLBACK when iv_rank is None). The **two-part
  score**: **Thesis-Fit** = `fit_directional(net_delta, view)` (per-SHARE scale ~±0.5,
  tanh-clamped; a bullish structure scores high in a high-conviction bull view, a
  delta-neutral structure scores high only at low conviction) + `fit_vol(net_vega,
  vol_regime)` (long-vega fits LOW iv, short-vega fits HIGH); **Structural-Quality** =
  liquidity (`scoring.norm_liquidity` across legs) + R:R/capital-efficiency +
  breakeven-vs-EM + PoP. **Quality-gated grading (2026-06-30 — the grade reflects trade
  QUALITY, not view-fit):** `score_strategy` composite is **quality-dominant**
  (`0.7·quality + 0.3·fit`; fit is a ranking tiebreaker, no longer half the grade), and the
  **grade is capped by per-family HARD GATES** on liquidity, R:R (or capital-efficiency for
  naked shorts, whose R:R is undefined), and PoP — `GATE_BARS`/`gate_profile`/`evaluate_gates`.
  A trade that FAILS any minimum bar → **Weak** (composite capped ≤`GATE_FAIL_CAP`39) + a
  **`grade_reason`** naming the failed dims (e.g. "Fails: liquidity, PoP"); pass all mins →
  **Good** (≥`GOOD_MIN`58)/**Marginal**; pass the **excellent** bars on every gated dim +
  composite ≥`STRONG_MIN`78 → **Strong** (genuinely rare). **Since 2026-08-06 `compute.swing_scan`
  DROPS the Weak ones before publishing, so the Finder's table no longer renders a "Fails: …" row
  — the grade machinery still runs and now DECIDES the cut, and the count of dropped rows surfaces
  in the status line instead.** Bars are per-family (credit = high
  PoP/low R:R; long = low PoP/high R:R with unbounded-profit auto-passing reward; naked =
  capital-efficiency, so its low cap-eff keeps it below Strong by design). **Making the
  liquidity gate real** required carrying `bid`/`ask`/`volume`/`oi` onto the normalized legs
  (`strategy_scanner._leg_from` + the adapters' short legs; `scanner_engine.build_iron_condors`
  now forwards put-short + call-short `bid`/`ask`/`volume`/`call_*` so the IC liq gate isn't
  inert). `q_liq` degrades to 50 for a leg genuinely missing bid/ask (no false-fail). The page
  shows a **color-coded Grade** (Strong/Good→green, Marginal→amber, Weak→red via
  `strategy_table.grade_class`) with the `grade_reason` in a tooltip. `score_all` scores +
  sorts desc; all per-signal defensive. Design/plan:
  [design](docs/plans/2026-06-30-swing-quality-gated-grading-design.md) /
  [plan](docs/plans/2026-06-30-swing-quality-gated-grading.md).
- **`services/options_svc/compute.swing_scan`** now returns `{"signals", "view"}` and
  takes a `families` arg (None ⇒ all of DIRECTIONAL/VERTICAL/NEUTRAL). It keeps the
  existing fetch (chain/quote/spot/hist/tech/iv/dem), derives **`atm_iv` as a DECIMAL
  from the engine's authoritative dollar daily EM** (`atm_iv = dem·√365/spot`, sidesteps
  the percent/decimal trap — `run_iv_analysis.current_iv` is a PERCENT) + `em_1sd =
  dem·√dte_min`, infers the view, builds the selected families (`screen_spreads` run ONCE
  and shared between the VERTICAL credit set + the NEUTRAL iron condors), scores via
  `strategy_scoring.score_all`, and early-returns `{"signals": [], "view": {}}` on a
  missing chain. `strategy_scanner`/`strategy_scoring` are imported LAZILY (the documented
  cross-app `scoring`-collision discipline). The handler adds `families` to
  `_SWING_DEFAULTS` and caches `view` alongside `signals` under the unchanged
  `cache:options:swing`.
- **Page** `webgui/pages/options/swing.py` + PURE `strategy_table.py`: a **Strategy-families
  multiselect** (Directional/Spreads/Neutral; default all; empty ⇒ all, with an explicit
  notify), an inferred-**view banner** (`view_banner_text`), and strategy-agnostic
  **columns/rows** (`strategy_columns`/`strategy_rows` — Strategy/Bias/Legs/Debit-Credit/
  Max P/Max L/R:R/PoP/BE/Score/Grade, with `:class` finite-map coloring by score+bias,
  Tailwind-first). The legacy delta/credit gates moved into an **"Advanced — credit
  spreads"** expander (they only constrain PCS/CCS). Row-click feeds `detail_signal(sig)`
  (fills `credit`/`breakeven` from the normalized keys) to the shared detail panel.
  **Handoff** (`handoff.py`): `send_signal_to_calculator` + the extended
  `signal_to_em_payload` route the canonical `legs` to the Calculator / Expected-Move for
  ALL types (back-compatible with old spread dicts lacking `legs`); `add_strategy_row_actions`
  shows Paper-trade when `row._allow_paper` — credit spreads (PCS/CCS/IC) **plus the defined-risk
  debit structures (LONG_CALL/LONG_PUT/BULL_CALL/BEAR_PUT)** as of 2026-07-13 (the ledger grows a
  legs-based DEBIT trade; naked shorts stay excluded — undefined risk).
- **Scope:** single-symbol; Phase 1 = Directional + Verticals + Neutral(IC). **Phase 2**
  (condor/butterfly/iron-fly) + **Phase 3** (diagonals — multi-expiration) are planned in
  the plan doc. Built subagent-by-subagent (TDD, two-stage spec+quality review per unit +
  a final holistic review). Test counts: strategy_scanner **18** + strategy_scoring **35**
  + options_svc **313** + webgui **650** green. **Live-verified** end-to-end: the REAL
  `compute.swing_scan` against the live proxy (SPY + NVDA) produced `{signals, view}` with
  an inferred bearish view and bearish structures (LONG_PUT/BEAR_PUT) correctly ranked on
  top, all scored + sorted. Design/plan:
  [design](docs/plans/2026-06-30-multi-strategy-swing-scanner-design.md) /
  [plan](docs/plans/2026-06-30-multi-strategy-swing-scanner.md).


**P0** — `theme.py` ships the
Tailwind token vocabulary (`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/`BTN`/`BTN_PRIMARY`/
`STRATEGY_BTN` + the semantic state-color tokens `TXT_POS`/`TXT_WARN`/`TXT_NEG`/
`TXT_NEUTRAL` + `STATE_TEXT_CLASSES` + the 3D-button tokens `BTN_3D`/`BTN_3D_DANGER`) + the
**`QUASAR_INTERNAL_CSS`** escape-hatch block (the field/tab/menu internals scoped under the
`.calc-v2`/`.strategy-menu-btn`/`.leg-*` hooks). **The legacy `DASHBOARD_CSS` was DELETED in P4
(its last consumer, Trade, flipped) — `theme.py` is now tokens + `QUASAR_INTERNAL_CSS` only.**
**P1** — the **nav shell** (`main.py`) is fully Tailwind; `_NAV_CSS` is now
Quasar-internal-only. **P2** — the shared **`pages/options/*` helpers**
(`detail.py`/`header.py`/`overlay.py`) are `.style()`-free: dynamic data-driven colors are
**palette-mapped** (a finite state/label → a fixed token — detail tiles via `TXT_*`; the
header VIX-regime badge + sentiment dot via local label→class maps
`regime_badge_class`/`sentiment_dot_class`), and reactive recolors use
`.classes(remove=…, add=…)` to avoid class accumulation; `leg_editor.py`/`strategy_menu.py`
were already inline-style-free. **P3a** — the six **signal-table screens**
(`scanner`/`swing`/`captured`/`paper`/`portfolio`/`rescue`) are free of `.style()` AND every
Vue `:style=` slot binding: dynamic **table-cell** colors stamp a Tailwind **class** field
from a finite-set map (`score_zone_class`/`rec_class`/`pnl_class`/`verdict_class`/
`heat_bg_class`/`heat_border_class`/`cash_class`) and bind `:class` (JIT-generated); the **3D
gradient buttons** use `BTN_3D`/`BTN_3D_DANGER` (`color=None`); per-page `ui.add_css` is
slimmed to Quasar-table-internals (cell padding, sticky `thead`, `.q-table__middle`, scanner
`.q-tab*`). A `test_no_inline_style.py` guard pins all helper + 3a pages. **P3b** — the
**Calculator + Simulator** (the heaviest `DASHBOARD_CSS` consumers) now use the tokens:
`.calc-card`→`CARD`, `.cv2-btn`→`BTN`, `.cv2-btn-primary`→`BTN_PRIMARY`, `.calc-eyebrow`→
`EYEBROW`, `.strategy-menu-btn`→`STRATEGY_BTN` (in the shared `strategy_menu.py`), title →
`LABEL`, calc summary tiles palette-mapped via `tile_color_class`; both pages now inject
`QUASAR_INTERNAL_CSS` (NOT `DASHBOARD_CSS`) and keep `.calc-v2`/`.strategy-menu-btn`/`.leg-*`
ONLY as **scope hooks** for the Quasar field/tab/menu internals; the dead `CALC_CSS` was
deleted. The Calculator **P&L heatmap is a raw `ui.html()` grid → out of scope** (documented).
**`DASHBOARD_CSS` is now consumed ONLY by `trade.py`** — Phase 4 flips Trade, then the cleanup
deletes the now-dead `.calc-card`/`.cv2-btn*` semantic rules. **P3c** — **Gamma + Expected-Move**:
gamma's 2 dynamic colors palette-mapped (hedge tile → `TXT_*`; collector status bar → a local
`status_color_class` map, reactive `remove/add`) and its 6 **panel-flex** `.style()` → a runtime
arbitrary `flex-[{w}_1_0%]` class (the documented **continuous-value** exception — no finite
palette — reset via tracked-previous `_set_flex_class`); Expected-Move was already clean.
Highcharts option dicts + the Explain/Analyze HTML (Tier-2) + `EXPLAIN_CSS` (styles a `ui.html()`
fragment) stay **out of scope**. **Phase 3 (every Options screen) is COMPLETE.** **P4** —
**Trade** (the LAST `DASHBOARD_CSS` consumer) converted: `.calc-card`→`CARD`, `.calc-eyebrow`→
`EYEBROW`, `.cv2-btn-primary`→`BTN_PRIMARY`, `.calc-v2` kept as hook + `PAGE`; its 10 `.style()`
colors palette-mapped via **LOCAL** maps (`verdict_text_class`/`bias_text_class` — the verdict
3-set `#2e7d32`/`#f9a825`/`#c62828` is DARKER than `TXT_*`, deliberately not shared — +
`markov_band_bg_class` for the 5-band chip; Highcharts `_MK_*` untouched), reactive verdict/chip
labels via `remove/add`. Then the **LEGACY CLEANUP**: `DASHBOARD_CSS` had zero consumers → **DELETED**
from `theme.py`; the dead `verdict_color`/`bias_color` hex fns + `*_COLOR` constants removed;
`test_theme.py` now asserts `not hasattr(theme,"DASHBOARD_CSS")`; the "App theme" section + example
rewritten to the token reality. **`theme.py` = tokens + `QUASAR_INTERNAL_CSS` ONLY** — the migration's
payoff. **The entire Options section + Trade are Tailwind-only** (587 green; Calc/Sim un-regressed
post-deletion). **P5** — **Sentiment + Sector Rotation** (the heaviest phase, ~58 `.style()`):
static widths/flex → arbitrary Tailwind; ~20 dynamic colors from finite sets → **LOCAL Tailwind
class maps** (these pages keep their OWN palette `#66bb6a`/`#ef5350`/`#ffd54f`/`#9e9e9e`/`#3fb6c7` —
yellow/cyan/flat differ from `TXT_*`, so deliberately NOT shared; pages do NOT adopt `PAGE`/`CARD` —
their look is preserved); the `.sent-sectors` `ui.add_css` block → Tailwind row borders/hover; the
**auto-refresh** in-place recolors (traffic tiles bg + bias/regime/rotation/headline labels) use
`remove/add` (verified live: no class stacking across refresh cycles). Gauges + history/RRG
Highcharts charts stay **out of scope**. **P6** — **Portfolio**: the proxy/stream status-bar colors
(local `#2e9e6b`/`#e24b4a`/`#888888`) on persistent labels via `remove/add`; static pre-wrap →
`whitespace-pre-wrap`. **P7** — **Driver**: the grade + control-state **badge backgrounds**
(`grade_bg_class` 5-set / `control_bg_class` 3-set, rebuilt per repaint → `add=`) and the perf **P&L
table cell** — a Vue `:style=` slot → **`:class`** with a stamped `_pnl_class` row field (a guard test
pins the slot references it); `DRIVER_CSS` kept (Quasar-internal sticky-thead). **P8** — **Status**
(3 static `min-w-[…]` widths; Quasar `color=` props left), and **Settings/Terminate/Manuals** were
already `.style()`-free; **EOD**'s `EOD_CSS` styles a `ui.html()` fragment → **out of scope**. All
five pages joined the `test_no_inline_style.py` guard. **✅ The migration is COMPLETE — Phases 0–8,
the entire webgui is Tailwind-only (607 green, live-verified across every page).** Design/plan docs:
[design](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md) + the per-phase plans
`2026-06-28-tailwind-first-ui-migration-{plan,phase2,phase3a,phase3b,phase3c,phase4,phase5,phase6-8}-plan.md`.
