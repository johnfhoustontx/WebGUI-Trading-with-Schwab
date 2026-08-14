# CHANGELOG — WebGUI Trading with Schwab

The running log of dated session entries ("**Last updated** / **Prior —**") that used to sit at the top of [CLAUDE.md](../CLAUDE.md). Newest first — **append new entries at the top**. Durable architecture + conventions stay in CLAUDE.md.

---

**Last updated:** 2026-08-14 (**the Market Regime panel stopped spending all its ink on the part
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
