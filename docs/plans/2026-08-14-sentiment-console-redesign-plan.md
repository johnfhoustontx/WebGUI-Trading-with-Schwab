# Plan — `/sentiment` Market Regime Console redesign

**Design source of truth:** [`docs/design/2026-08-14-market-regime-console/`](../design/2026-08-14-market-regime-console/)
— the handoff bundle (README spec + `Regime Dashboard.dc.html` prototype + full-page screenshot),
copied into the repo so this plan does not depend on a file in Downloads.

**Status:** Phase 0 done (results in §4). Phases 1–7 not started.

---

## 1. What this actually is

A high-fidelity redesign of the **top** of `/sentiment` into a single-screen dark "console":
header bar → three cards (Sentiment · Trend · Signals) → a regime block (confidence dial +
diagnostic tags on the left, regime-share table + callout strip on the right) → footer bar.

**It is an evolution of the current page, not a foreign design.** The evidence is in the handoff
itself: its sample numbers are this app's live numbers (trend day 68 vs live 67.8, month 83 vs
live 82.7, regime shares 38.8/28.6/27.7/4.9/0.0, "leads by 10.2 pp", "tightest today 0.2 pp",
tags "EMA FLAT" / "BALANCED PROFILE 0.53" / "ADX RISING 36"), and its four Signals cells reuse
this page's own descriptors verbatim — "MARKET DIRECTION", "STRENGTH & MOMENTUM", "PREVIOUS
CLOSE", "VS YESTERDAY". Treat it as a re-encoding of data the app already computes, which is why
the data audit below comes out overwhelmingly green.

### Decisions taken (asked and answered 2026-08-14)

| Decision | Choice | Consequence for this plan |
|---|---|---|
| Width | **Fluid up to 1440px** | The design's fixed pixel columns become proportions with a `max-w-[1440px]` cap. No horizontal scroll; degrades below. |
| Visual language | **This page only** | The console palette lands in `config/theme.toml` as its own section, scoped to `/sentiment` by a class hook. `/sentiment` becomes a deliberate outlier; nothing else moves. |
| Content the design omits | **Keep below** | The console becomes the top of the page. The "Daily Sentiment & Trend" intraday graphs, the Components popup, the status bar and Refresh survive underneath, unstyled by this work. |

### What this replaces

Two things shipped **today** are superseded on this page, and that should be a conscious call:

* **The concentric Day/Week/Month rings** (`webgui/pages/rings.py`, shipped this morning) are
  replaced by the design's horizontal timeframe meters. `rings.py` is used **only** by
  `/sentiment`, so it becomes dead code — Phase 7 decides delete vs keep-for-reuse.
* **The ranked regime panel** (`webgui/pages/regime_mix.py`, committed hours ago) is *not* thrown
  away — the design's regime-share table is a near-superset of it, so this is mostly additive.
  Already built and reusable: ranking, share bars normalised to the leader, self-scaled
  sparklines, change-vs-session-open, the zero/dormant state, and `lead_margin()` — which is
  exactly the design's "LEAD +10.2 pp / TIGHTEST TODAY 0.2 pp" stat pair.

---

## 2. Data audit — every element against what the app publishes today

Read live from `cache:sentiment:composite` + `:regime` + `:regime_history` on 2026-08-14.
**✅ = exists · ➕ = derive/author in Tier 1 · ⚠️ = needs an additive Tier-2 field.**

| Design element | Source today | |
|---|---|---|
| Sentiment day / week / month meters | `sentiment_arcs(live, snaps)` — already rescales composite 0–10 → 0–100 | ✅ |
| Sentiment hero + "LONG 7.28" pill | `live.composite.total_score` (a **string**), `derived.bias` | ✅ |
| "▲ +13 vs WEEK" delta | `day − week` off the arcs | ➕ |
| MODEL CONFIDENCE 70% | `live.composite.aggregate_confidence` (0.9 live) | ✅ |
| Trend day / week / month meters | `derived.trend.smoothed_score`, `trend_7d`, `trend_30d_ago` | ✅ |
| Trend WEEK "NO READ" hatched state | `trend_arcs` already returns `None` for a zero-confidence horizon | ✅ |
| Trend verdict + guidance copy | `derived.trend.label` / `.description` | ✅ |
| Signals BIAS / SIGNAL / YESTERDAY / CHANGE | `tiles()` + `SIGNAL_TILE_DEFS` (descriptors already match the design) | ✅ |
| 3D ROC / 5D ROC / 20D Z **numbers** | `derived.velocity.text` is a formatted string — but `scoring/composite.velocity()` already **returns** `roc_3d`/`roc_5d`/`z_20d` numerically; the service stringifies them | ⚠️ |
| Divergence breadth-vs-rotation **numbers** | `derived.divergence` is a string; `scoring/composite.divergence()` returns only that string, though the service holds the named scores it passed in | ⚠️ |
| Regime name + confidence dial value | `cache:sentiment:regime` → `committed_label`, `confidence` | ✅ |
| Diagnostic tags | `regime.evidence` — plain strings, with **no severity** and no label/value split | ⚠️ |
| Regime share / sparkline / change columns | `regime_mix.rank_rows()` | ✅ |
| LEAD + TIGHTEST TODAY stat pair | `regime_mix.lead_margin()` | ✅ |
| Callouts DOMINANT / BIGGEST MOVE / EMERGING | derive from `rank_rows` (max share · most-negative change · largest rise from zero) | ➕ |
| Regime note lines ("CHOP · NO EDGE") | none — static copy per regime key | ➕ |
| Header "DATA AS OF … STREAMING/STALE" | `composite_at` + a freshness threshold | ➕ |
| Header "SESSION / US EQUITIES · RTH" | `shared.market_calendar` (already imported by `webgui/pages/sentiment.py`) | ✅ |
| Footer disclaimer | static copy | ➕ |

**Only three ⚠️ rows, and all three are additive Tier-2 publishes of numbers the engine already
computes.** CLAUDE.md explicitly permits this: *"only when no clean state exists, refactor the
Tier-2 source to emit one (allowed — this is the documented exception to webgui-only)."*

**Do not parse the display strings page-side.** `derived.velocity.text` and `derived.divergence`
are human copy; deriving meters from them would couple the UI to a sentence's punctuation.

---

## 3. Technical approach, and the house rules it has to satisfy

### 3.1 The Tailwind-first mandate is the single largest cost driver

The prototype is 100% inline styles. This repo **bans** `.style()`, `:style=` and inline `style=`
in `webgui/pages`, guarded by `tests/test_no_inline_style.py`. Every one of the handoff's
declarations becomes a Tailwind class or a pure-SVG attribute. Budget for this — it is not a
mechanical find-and-replace.

### 3.2 The glow vocabulary — measured, and it is a non-issue

The design is glow-only (no elevation shadows), so this was the main technical risk. **Phase 0
settled it: every shape generates** — see the 0.2 table for the measured list. The working rules:

* Glows: `[text-shadow:…]` and `shadow-[…]` both accept `#rrggbb`, `#rrggbbaa` **or** `rgba()`.
  Use `rgba()`/8-digit hex only when you want alpha; `_rgba()`/`_hex_rgb()` helpers already exist.
* Gradients: `radial-`, `linear-` and `repeating-linear-gradient` arbitraries all generate,
  rgba stops included — so the card corner glows, page wash and "NO READ" hatch are all plain
  Tailwind.
* `border-[#hex]/40`, `tracking-[.16em]`, `gap-px`, and arbitrary `grid-cols-[…]` (incl. `minmax`)
  all work.
* Arbitrary values may not contain **spaces** — underscores are the escape.
* **`var()` remains the one trap**, unchanged: it silently produces no rule, which is why the nav
  pill is hand-written CSS. Do not reach for `var()` in a class.

### 3.3 What is built as what

| Component | Technique | Why |
|---|---|---|
| Card shells, hairline grids, chips, readout matrix, callout strip, header/footer | NiceGUI + Tailwind tokens | House standard. Hairline grids are `gap-px` over a tinted background — exactly the handoff's own technique. |
| Timeframe meters, segmented confidence meter, bipolar ROC meters | NiceGUI + Tailwind | Nested divs with a percentage width; the handoff itself recommends discrete flex cells over its `repeating-linear-gradient` hack. |
| **Confidence dial** | Pure SVG builder | **Reuses `rings._point` / `_arc_path`**, which are already written and tested. The dial is one 0.56 arc — the same primitive the rings draw. |
| Regime share bars + sparklines | Extend `regime_mix.py` | Already built and tested. |
| `pulseDot` keyframes | The page's one `ui.add_css` block | Precedent: the market ticker's marquee keyframes is documented as "the ONE `ui.add_css` escape hatch". |

**Every SVG builder inherits the sanitizer constraints** proven by `rings.py`: no `<style>`, no
`<filter>`, no `<use>`, `dy` not `dominant-baseline`. The dial's spec asks for
`filter: drop-shadow(...)` on the arc — **that will be stripped.** Substitute the rings' proven
technique: a wide translucent copy of the arc drawn underneath a normal-width bright one. Mirror
`test_rings.py`'s DOMPurify allowlist guard for each new builder.

### 3.4 Fonts

Rajdhani (500/600/700) is new; IBM Plex Mono is already the app's face. Precedent exists for a
second font: `[brand].font_url` loads Montserrat separately from `[typography].font_url`. Add
Rajdhani the same way, as a display font in the new theme section. Note it is a Google Fonts
fetch — the app is localhost and already does this for the brand face, so this adds no new class
of dependency, but it does degrade to a fallback offline.

### 3.5 Fluid-to-1440

Convert the handoff's fixed pixel columns to proportions, capped at `max-w-[1440px]`:

* top row `1fr 1fr 1.05fr` → keep as a ratio (already proportional).
* regime block `396px 1fr` → `minmax(340px,396px) 1fr`; the dial's SVG scales by viewBox.
* share table `190px 1fr 132px 74px` → keep the fixed cells, let the sparkline column flex.
* **The viewBox-scales-the-text trap applies to every new SVG** (learned on `regime_mix.py`
  hours ago): an SVG at `width:100%` scales its *text* too. Cap each builder's rendered width or
  size the text against the eventual render, not the viewBox.

---

## 4. Phases

Each task follows the house pattern: TDD the pure builders, keep `render()` thin, then verify
live in dev. No phase leaves the page broken — the console is assembled behind the existing page
and swapped in at Phase 6.

### Phase 0 — Groundwork and spikes — ✅ **DONE 2026-08-14**

All four spikes measured live against the running dev app, not reasoned about. **Every styling
technique the design needs is expressible under the Tailwind-first rule**, which removes the
largest unknown from Phases 3–5.

**0.1 Handoff bundle** copied into `docs/design/2026-08-14-market-regime-console/`. ✅

**0.2 Glow + gradient vocabulary — everything generates.** A probe injected each candidate class
into the live page and read back `getComputedStyle`. All 15 generated:

| Class shape | Result |
|---|---|
| `[text-shadow:0_0_12px_#35d68a]` · `…rgba(53,214,138,0.55)]` | ✅ both |
| `shadow-[0_0_16px_…]` with `#rrggbb`, `#rrggbbaa`, **and** `rgba()` | ✅ all three |
| `shadow-[0_0_18px_-6px_#35d68a]` (negative spread, the exact live shape) | ✅ |
| `bg-[radial-gradient(420px_200px_at_0%_0%,rgba(…),transparent_70%)]` | ✅ — the card corner glows and page wash |
| `bg-[linear-gradient(160deg,rgba(…),rgba(…))]` | ✅ — the card shell |
| `bg-[repeating-linear-gradient(135deg,rgba(…)_0_6px,transparent_6px_12px)]` | ✅ — the "NO READ" hatch |
| `border-[#35d68a]/40` · `tracking-[.16em]` · `gap-px` | ✅ |
| `grid-cols-[minmax(340px,396px)_1fr]` · `grid-cols-[190px_1fr_132px_74px]` | ✅ — the fluid + table grids |

> **Two stale comments corrected as part of this task** (both measured false):
> `sentiment.py` claimed *"box-shadow arbitraries need the rgba() form, not a hex"* — hex works
> fine, the rgba is only for the alpha; and `market.py` claimed *"only var()/rgba() ones are
> unsafe"* — the rgba half was an over-generalisation from the var() case. **Only `var()` is the
> trap**, which is why the nav pill remains hand-written CSS.

**0.3 Rajdhani loads; the fallback matters.** The page already pulls **two** Google Fonts
stylesheets (IBM Plex Sans+Mono, and Montserrat 800 for the brand wordmark), so a third follows an
established, doubled precedent — no new class of dependency. Measured at 40px over the string
`MARKET REGIME CONSOLE 0123456789`:

| | width |
|---|---|
| Rajdhani **before** loading | 775.16px — *identical to a nonsense font name* |
| Rajdhani **after** loading | **618.28px** (17% narrower — the condensed display look) |
| IBM Plex Sans (the fallback) | 747.97px |

> ⚠️ **`document.fonts.check('16px Rajdhani')` returned `true` while the font was demonstrably
> NOT loaded.** Never gate on it — measure a rendered width against a nonsense family instead.
>
> **Fallback:** `'Rajdhani','IBM Plex Sans',system-ui,sans-serif`. Note the fallback is **21%
> wider** than Rajdhani, so every uppercase letter-spaced heading must tolerate that much reflow
> before the webfont arrives (`display=swap`) — do not pack headings tight to the Rajdhani metric.

**0.4 The dial works from `rings.py`'s tested helpers, and the handoff's two hacks both drop out.**
`rings._point` already measures clockwise from 12 o'clock, so the design's *"rotate the whole SVG
−90°"* is unnecessary; and the halo idiom replaces the `filter: drop-shadow()` the sanitizer would
strip. Verified through the **production `setHTML` path**: the markup survives intact (3 circles,
2 paths, 3 texts; `d`/`stroke`/`stroke-width`/`opacity`/`dy` all kept) and the rendered arc
measures **324px against an expected 324px** — matching the handoff's own `stroke-dasharray 323.7`.

> ⚠️ **Edge case found: confidence `1.0` renders NOTHING.** A 360° sweep's start and end points are
> identical, and an SVG `A` command between identical points draws nothing —
> `getTotalLength()` returns **0**, so a 100% dial would show an empty ring. Phase 3.4 must cap the
> sweep just below 360° or split it into two arcs. (`0.0` correctly draws track-only; `0.004`
> upward draws.)

### Phase 1 — Tier-2 additive fields *(small, `sentiment_svc`)*
1.1 Publish `derived.velocity.values = {roc_3d, roc_5d, z_20d}` alongside the existing `text`.
1.2 Publish `derived.divergence_detail = {high:{name,score}, low:{name,score}}` alongside the
    existing string.
1.3 Publish `regime.evidence_detail = [{label, value, severity}]` alongside `evidence`, severity
    from the classifier (adverse evidence → `warn`).
All three are **additive** — existing readers untouched, contracts extended optionally. Tests in
`services/sentiment_svc/tests`. Requires a `sentiment_svc` restart to appear.

### Phase 2 — Theme tokens *(small)*
2.1 New `config/theme.toml` section for the console palette (surfaces, hairlines, the six text
    tints, the data colours, the regime hues incl. Breakout's muted `#6a5c33`).
2.2 Token constants + the `.console` scope hook, following `pages/options/theme.py`'s pattern.
2.3 The `pulseDot` keyframes in the page's single `ui.add_css`.

### Phase 3 — Shared primitives *(medium — the bulk of the new pure code)*
3.1 `timeframe_meter` — label · track · fill+glow · end marker · value; plus the **hatched
    "NO READ"** state and the shared 0/25/50/75/100 ruler row.
3.2 `segmented_meter` — N discrete cells (model confidence).
3.3 `bipolar_meter` — centred zero line, fill extending either side (ROC / z).
3.4 `confidence_dial` — SVG, reusing `rings._arc_path`; layered-stroke halo, centred label stack.
3.5 `hairline_grid` / `card_shell` / `chip` / `stat_cell` — the repeated chrome.
Each is a pure builder with unit tests, including its no-data state.

### Phase 4 — The three top cards *(medium)*
4.1 Sentiment card — hero, bias pill, delta, three meters, model-confidence footer, link.
4.2 Trend card — hero, verdict block, the NO-READ week meter, link.
4.3 Signals card — 2×2 readout matrix, the three ROC/z meters (Phase 1 data), divergence alert
    with its two-bar mini chart.

### Phase 5 — The regime block *(medium — most logic already exists)*
5.1 Extend `regime_mix.py`: per-regime note copy, and `callouts()` for DOMINANT / BIGGEST MOVE /
    EMERGING.
5.2 Regime-share table in the design's four-column grid, reusing `rank_rows` and the existing
    bar/sparkline/zero-state logic.
5.3 Confidence-dial card + the LEAD / TIGHTEST stat pair (`lead_margin` already returns both).
5.4 Diagnostic-tags card off `evidence_detail`, with the info/warn split.
5.5 Callout strip.

### Phase 6 — Page assembly *(medium)*
6.1 Header bar (live dot, title, session chip, data-as-of chip).
6.2 Footer disclaimer bar.
6.3 Compose the console at the top of `/sentiment`; **keep the intraday graphs, Components popup,
    status bar and Refresh below it**, untouched.
6.4 Wire every value into the existing `_apply()` repaint path — in place, no re-mount, so the
    2-minute refresh does not rebuild rows.

### Phase 7 — States, cleanup, verification *(small)*
7.1 Loading skeleton; stale-data header chip (amber + "STALE").
7.2 Decide `rings.py`: delete, or keep as a reusable primitive with its tests.
7.3 Full webgui suite; `test_no_inline_style.py` covers the new modules; DOMPurify guards mirrored.
7.4 Live-verify in dev at several window widths; then CLAUDE.md + CHANGELOG.

---

## 5. Risks and open questions

| Risk | Handling |
|---|---|
| **Scale collision.** The console shows sentiment 0–100 while the intraday graph below it shows the same series 0–10. | Not introduced by this work — `sentiment_arcs` already rescales ×10 — but the two will now sit on one page. Flag the axis clearly, or rescale the graph. **Open question for the user.** |
| **Volume of styling.** ~12 components of dense, hairline-precise chrome, all re-expressed as Tailwind. | Phased; primitives (Phase 3) built once and reused. The main cost is Phases 3–5. |
| **`drop-shadow` on the dial is sanitizer-stripped.** | ✅ Resolved in 0.4 — layered-stroke halo, verified through the production `setHTML` path; arc measures exact. |
| **Dial at confidence 1.0 draws nothing** (360° sweep = identical endpoints). | Found in 0.4. Phase 3.4 caps the sweep below 360° or splits it; needs a unit test. |
| **SVG viewBox scales text.** | Cap rendered width per builder — the mistake already made and fixed on `regime_mix.py`. |
| **Rajdhani is a network font**, and its fallback is 21% wider. | ✅ Measured in 0.3. `display=swap` means headings reflow on arrival — do not pack them to the Rajdhani metric. |
| **Dead code.** `rings.py` loses its only consumer. | Explicit decision in 7.2 rather than silent abandonment. |
| **Design has no error/stale states beyond the two authored.** | The handoff names them as recommendations; Phase 7.1 implements stale + loading only. |

**Deliberately out of scope:** the sub-tabs (`/sentiment/sectors`, `/rotation`, `/rrg`,
`/momentum`), any other page, and responsive behaviour below the fluid cap (the page is already
desktop-only — its two intraday Highcharts render at a fixed 1105px and overflow a narrow
viewport regardless).
