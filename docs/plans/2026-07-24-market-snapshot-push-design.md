# Market Snapshot push (Discord + Telegram) — design

**Date:** 2026-07-24
**Branch:** `Using_Highcharts`
**Status:** approved, pending implementation plan

## Summary

A new scheduled push that fires **every :00 and :30 during the trading day** and
delivers **one combined PNG** to Telegram + Discord:

1. The **Market Dashboard** tile-grid (server-composed, visually equivalent to
   `/market`), followed by
2. A **"Market Read"** section with three panels — **Daily Market Trend**, **Daily
   Market Sentiment**, **Daily Market Regime** — each drawn as a **gauge + mini
   intraday sparkline** with a **static explainer + live read**.

No Claude cost (static explainers), zero new dependencies (reuses the existing
headless-Chrome HTML→PNG renderer).

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Dashboard image | **Server-composed infographic** — read `cache:market:dashboard`, render static HTML → PNG. Not a live-page screenshot (no webgui/browser-nav dependency; robust if the web app is down). |
| Packaging | **One combined infographic** — dashboard grid on top, three sub-component panels below. One attachment per channel per push. |
| Sub-component viz | **Gauge + mini intraday sparkline** per panel + explanation. |
| Explanation text | **Static explainer + live read** — a fixed sentence describing the metric + the live interpretation pulled from the caches. No API cost. |
| Schedule window | **08:30–15:00 CT**, every :00 & :30 (~14 pushes/trading day). Tunable via config. |
| Host service | **`options_svc`** (owns the render + push + scheduled-slot machinery). |

## Host service — `options_svc`

`options_svc` already owns every rail this needs:

- `briefing_image.render_html_png` — headless-Chrome HTML→PNG, zero new deps
  (Chrome/Edge already on the box; PIL already in the venv).
- `push_notify` — `send_telegram_photo` / `send_discord_file`, config loading,
  the per-channel `flow_webhook`-style routing pattern.
- The scheduled-slot pattern — `action_alert_due` / `eod_summary_due` /
  `analyze_slot_due` gated on the existing scheduler tick.

market_svc *owns* the dashboard DATA but has none of the render/push machinery —
duplicating it there is pure cost. All data lives in Redis, so options_svc just
reads it (it already reads cross-domain caches).

## Data sources (all Redis reads, all defensive)

| Panel | Cache | Fields |
|---|---|---|
| Dashboard grid | `cache:market:dashboard` | frames → tiles (`symbol`, `description`, `last`, `change`, `color_state`) |
| Daily Market Trend | `cache:sentiment:composite` → `derived.trend` | `score` 0–100, `label`, `description`, `evidence` |
| Daily Market Sentiment | `cache:sentiment:composite` → `live.composite` | `total` 0–10, `bias` |
| Daily Market Regime | `cache:sentiment:regime` | `label`, `confidence`, `memberships` (5 keys), `transition`, `unclear` |
| Trend / Sentiment sparklines | `cache:sentiment:intraday_history` | `points[{ts, trend 0–100, sentiment 0–10}]` |
| Regime mix sparkline | `cache:sentiment:regime_history` | membership mix over the day |

Any missing/empty cache → that panel degrades to a "no data" placeholder; the push
still goes. The whole handler is best-effort and never raises into the scheduler.

## Rendering — server-composed HTML infographic → PNG

A new **pure** builder `compute.market_snapshot_doc(dashboard, trend, sentiment,
regime, intraday, regime_hist)` returns a self-contained dark HTML document (same
idiom as `compute._analyze_doc`):

- **Dashboard section** — lay the framed category tiles out as static HTML, colored
  by each tile's `color_state` (already computed by market_svc; the color→class map
  is small and mirrored here). Visually equivalent to `/market`, no Highcharts.
- **Three "Market Read" panels** — each is:
  - an **inline-SVG semicircle gauge** showing the current value (Trend 0–100,
    Sentiment 0–10, Regime confidence + committed label) — reuse/port the gamma
    briefing's inline-SVG approach (`_ladder_svg` / `_bias_meter_html`);
  - a **small inline-SVG sparkline** built from the intraday points, colorized
    green/yellow/red by band (Trend bands ≤30/≤70; Sentiment ≤4.5/≤6.5; Regime =
    the membership mix as a stacked mini-area);
  - one **static explainer** sentence + the **live read** text (state label,
    regime description + transition, sentiment bias / trend evidence).

Rendered by the existing `briefing_image.render_html_png(html)` (width tuned for the
tile grid), auto-cropped, size-guarded. Pure SVG/HTML + JS-free → deterministic and
unit-testable.

## Scheduling + delivery + config

- **New slot gate** `scheduler.market_snapshot_due(now, ran_slots)` — trading-day +
  holiday gated, fires once per :00/:30 slot within a grace window (mirrors
  `action_alert_due`). Window from config, default 08:30–15:00 CT.
- **Handler** `handlers.run_market_snapshot(bus, slot)` → read caches → build doc →
  `render_html_png` → `push_notify.send_market_snapshot(...)` (Telegram photo +
  Discord file) → cache the last payload at `cache:options:market_snapshot` for
  inspection. Best-effort after the render; a push failure can never break the tick.
- **Config** — a `market_snapshot` block in the gitignored `shared/notifications.json`
  (+ placeholder in the committed `.example.json`):
  - `enabled` (default `true`), `start` / `end` CT window.
  - Discord routes to an optional **`discord.market_snapshot_webhook_url`**, falling
    back to `discord.webhook_url` — consistent with the `flow_*_webhook_url`
    per-channel pattern. (The real webhook value lives ONLY in the gitignored config,
    never in this doc.)
  - Telegram uses the shared `telegram.bot_token` / `chat_id`.
  - Gated by the master `enabled` too.
- **No Claude cost** — static explainers.

## Testing

- Pure builders unit-tested TDD: `market_snapshot_doc` HTML, gauge SVG, sparkline
  SVG, `market_snapshot_due` cadence (once-per-slot, grace, holiday/weekend gate),
  `send_market_snapshot` gating (master `enabled`, block `enabled`, missing creds →
  no-op).
- End-to-end: invoke the handler against live caches, confirm a PNG renders (Chrome
  present) and the payload caches, then a real dry-run push to the configured
  channel.
- **Restart `options_svc`** to pick it up.

## Out of scope (YAGNI)

- Live-page screenshot of `/market` (fragile; rejected in brainstorming).
- Claude-written narrative per component (~39 calls/day; the caches self-describe).
- A Settings-page toggle (config-file gated, like the other push features).
- SMS delivery (an image summary over SMS is not useful).
