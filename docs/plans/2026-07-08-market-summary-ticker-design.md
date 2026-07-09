# Market Summary Ticker — Design

**Date:** 2026-07-08
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorming complete) — ready for implementation plan.

## Goal

A fixed scrolling ticker (marquee) pinned to the **bottom of every page** that gives an
at-a-glance market read synthesized from the app's own live data — the Market Dashboard
tiles, the Sentiment composite, and the five-state Market Trend. **Hybrid content** (user
choice): it scrolls a short **Claude-written verdict** (the "why", refreshed periodically)
**plus live, color-coded data items** (the fast-moving numbers, updating every few seconds),
looping.

Example scroll:
> ⚠ Cautious, narrow tape — mega-cap/semis holding the index while breadth weakens; hedging
> rising · 🔴 Cautious 3.9/10 · Trend Neutral 42.7 · Breadth 0.41:1 · VIX 16.9 ▲ · SKEW 150 ·
> P/C 1.34 · SPX −0.3% · NDX +0.3% · Semis SMH +2.5% · XLE +2.1% · Net −1,148

## Architecture (matches the hybrid choice)

Two content streams, combined at render time:

1. **Live data items — rule-based, Tier-1 (webgui).** A PURE builder reads the
   already-published `cache:market:dashboard` + `cache:sentiment:composite` and composes
   bite-sized, tone-colored items. Updates live, version-gated, **zero API cost**. This is
   the fast path — it moves with the market.
2. **Narrative verdict — Claude, scheduled (market_svc).** `market_svc` builds a compact
   packet (key dashboard facts + sentiment + trend) and calls Claude for a 1–2 sentence
   verdict on a schedule, caching **`cache:market:summary`**. Reuses the proven **Gamma
   Analyze** pattern (lazy `anthropic` import, key via `ANTHROPIC_API_KEY` env →
   `shared/anthropic_key.txt`, Sonnet 5, thinking disabled, small `max_tokens`). Fully
   defensive: no key / SDK / API error → empty narrative, and the ticker simply shows the
   live items (never blocks, never blank-crashes).

```
market_svc scheduler loop (existing ~2s/5s poll)
  ├─ (existing) poll /quotes → publish cache:market:dashboard
  └─ (NEW) every ~20 min RTH / ~60 min off-hours:
        read cache:market:dashboard + cache:sentiment:composite
        → build_summary_packet → Claude → publish cache:market:summary

webgui _layout (shell on EVERY page)
  → render_ticker(): fixed bottom marquee bar
     • version-gated ui.timer reads market:dashboard + sentiment:composite + market:summary
     • ticker_items(...) builds live colored items (PURE)
     • scroll = [narrative] · [live items], rebuilt only when a cache version changes
     • gated by app_settings["ticker_enabled"] (Settings toggle, default on)
```

## Content

**Live items** (`ticker_items`, PURE) — each `{text, tone}`, tone ∈
`risk_on|risk_off|neutral|warn`:
- Sentiment: score/10 + bias (e.g. "Cautious 3.9/10").
- Trend: five-state label + 0–100 score (e.g. "Trend Neutral 42.7").
- Breadth: A/D ratio + net advancers.
- Volatility: VIX (+chg), VIX1D, SKEW.
- Options: cap-weighted put/call.
- Index: SPX %, NDX %.
- Leaders/laggards: top sector + top thematic mover (from the dashboard tiles), a notable
  country, credit/crypto if notable.
All derived from data ALREADY in the two caches — the builder is pure and unit-tested.

**Narrative** (`cache:market:summary` → `narrative`): a 1–2 sentence Claude verdict; the
"why" behind the numbers. Leads the scroll.

## Rendering

- Added in `main.py` `_layout` (the `@contextmanager` shell every page uses) as a fixed
  bottom bar (`ui.footer` or a `fixed bottom-0` element). The page content column gets bottom
  padding so the ticker never covers content; the "?" help fab stays clear of it.
- **Marquee** via a CSS `@keyframes` in the ONE documented `ui.add_css` escape hatch
  (a keyframe animation isn't a Tailwind utility): a `.ticker-scroll` inner span animated
  `translateX(100% → -100%)`, `animation-play-state: paused` on hover so you can read it.
  Everything else Tailwind-first; item colors **palette-mapped** from `tone` to fixed classes
  (green/red/amber/slate), exactly like the dashboard tiles (no `.style()`).
- Live update: a lightweight `ui.timer` (~3–5 s) reads the three cache **versions** (cheap
  `:ver` probes) and rebuilds the scroll text only when one changed — cheap despite being on
  every page. `@guard`-wrapped (dead-client safe).

## Cadences

- **Live items:** ~3–5 s, version-gated (no rebuild on unchanged data).
- **Claude narrative:** ~20 min during RTH, ~60 min off-hours (cached, `skip_unchanged`).
  Off-hours the market barely moves, so the verdict rarely changes.

## Control

- New `app_settings` keys: **`ticker_enabled`** (default `True`) + optional
  **`ticker_speed`** (scroll duration). A **Settings page toggle** shows/hides it, so it's
  dismissible. When off, `render_ticker` renders nothing.

## Contract

Additive `shared/contracts/market.py:MarketSummary` — `{narrative: str = "", generated_at:
str | None = None}`. Validates the envelope like `MarketDashboard`.

## Error handling

- Every layer defensive. No Claude key/SDK/API error → empty narrative → ticker shows live
  items only. Missing/cold caches → the ticker shows whatever items it can, or a neutral
  "Market data loading…" placeholder. The scheduler branch never raises out of the loop
  (matches the existing `except: log` discipline). The webgui timer is `@guard`-wrapped.

## Testing

- **Pure (TDD):** `ticker_items` (item composition + tone from sample cache dicts),
  `item_class` (tone→class map), `build_summary_packet` (compact facts from sample payloads),
  the narrative parse/defensiveness, `summary_due` cadence gating.
- **Service:** market_svc handler publish + `MarketSummary` gate; Redis-driven e2e for the
  summary publish (fakeredis + a fake Claude client).
- **Webgui:** pure builders unit-tested; the marquee/timer/footer wiring browser-verified
  (screenshot the ticker on a couple of pages); `test_no_inline_style` guard covers
  `ticker.py`.

## Out of scope (YAGNI)

- Per-item click-through, configurable item selection, multiple ticker rows.
- A full AI infographic (that's Gamma Analyze; this is a one-line verdict).
- Historical ticker/log.
