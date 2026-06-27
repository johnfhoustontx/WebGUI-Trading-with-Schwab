# EoD Report redesign — design

**Date:** 2026-06-27
**Status:** approved
**Page:** `/eod` + `/eod/detail` (`webgui/pages/eod.py`)

## Problem

The end-of-day report doesn't "make sense":

- It mixes **three different paper books** without distinction — the manual paper
  **ledger** (`options:paper_trades`, the user's "Send to Paper trade" trades), the
  manual paper **engine account** (`options:paper_account`, a separate balance), and
  it omits the **driver's isolated account** entirely.
- The **summary** is a thin 7-tile strip with no performance narrative.
- The **detail** report is flat point-in-time lists with no breakdown by trade type.
- There is no period view (today / week / month) and no easy navigation.

## Goals

1. **Verbose summary** anchored on **Daily / Weekly / MTD performance**.
2. **Detailed breakdown by trade type** — strategy, 0-DTE/Swing, status.
3. **Easy to navigate and read** — table of contents + collapsible sections.
4. Show **both books separately**: Manual paper ledger and Driver account.

## Architecture

Stays **pure-webgui** (honors the 3-tier rule — the page reads Redis caches and
builds an HTML fragment + CSS; no app-engine imports). New logic is **pure builders**
in `eod.py`, unit-tested with sample dicts. No new page or route.

**One small additive service change** (`services/options_svc/compute.py`): extend
`driver_account_view()` to also return `closed_positions` (closed driver positions
carry `exit_ts` + `realized_pnl` + `status`), so the driver book can be date-bucketed
**symmetrically** with the manual ledger (which already returns all trades via
`paper_trader.get_all_trades()`). This is the only non-page change; it requires an
`options_svc` restart.

### Data sources (existing caches)

| Cache | Book | Carries |
|-------|------|---------|
| `options:paper_trades` | Manual ledger | ALL trades: `entry_time`, `exit_time`, `realized_pnl`, `status`, `strategy` (PCS/CCS/IC), `trade_type` (0-DTE/SWING), `entry_credit_total`, `max_loss_total`, `quantity`, `unrealized_pnl` (when repriced) |
| `options:driver_paper_account` | Driver | `snapshot` (equity/session_pnl/realized/open_unrealized/open_count), `positions` (open) **+ new `closed_positions`** with `entry_ts`/`exit_ts`/`realized_pnl`/`status`/`strategy` |
| `options:driver_paper_perf` | Driver | all-time scorecard (win_rate, profit_factor, by_strategy, by_symbol, best/worst) |
| `options:scan` / `options:captured` | Activity | scanner signals / captured signals |
| `driver:approvals` | Driver | legacy morning-agent grade/status/proposed |

## Performance model

Three **calendar "to-date"** buckets, in CT:

- **Daily** = today.
- **Weekly** = Monday of this week → today (week-to-date).
- **MTD** = 1st of this month → today (month-to-date).

`period_buckets(trades, today_ct)` is the pure core: it classifies each trade into the
periods it falls in and aggregates. For **each book separately**, each period shows:

- **Realized P&L** — sum of `realized_pnl` for trades **closed** in the period
  (`exit_time`/`exit_ts` date in range).
- **Closed (W–L)** + **win rate** — closed-trade outcomes in the period.
- **Opened** — entries whose `entry_time`/`entry_ts` date is in the period.
- **Net credit collected** — sum of entry credit on those entries.

Plus a per-book **"as of now"** line (point-in-time, not period-bucketed): open
positions, open unrealized, session P&L, equity.

> **Data-state honesty:** with 0 closed trades, realized columns read `$0.00` and the
> periods lean on entries-opened + current open exposure. Builders render "no closes
> yet" cleanly (em-dash / `$0.00`, not a broken/blank look). They populate as trades
> close.

## Report structure

### Summary (`summary_fragment`)
1. Header + generated timestamp.
2. **Table of contents** (anchor jump-links).
3. **Performance overview** — Daily / Weekly / MTD, per book (Manual + Driver).
4. Headline tiles — equity, session P&L, open count, win rate (per book).
5. Activity counts — scanner signals, captured signals.
6. Link to the detailed report.

### Detail (`detail_fragment`)
1. Header + TOC.
2. **Performance** (Daily/Weekly/MTD, expanded).
3. **Trade breakdowns** — grouped **by strategy**, **by 0-DTE/Swing**, **by status**;
   each a table of `group | trades | open | closed | realized P&L | win %`, per book.
4. Full trade tables — manual ledger trades, driver positions (open + closed).
5. Scanner signals, captured signals, driver decision activity.

## Navigation & readability

- A **TOC** of anchor links (`<a href="#section-id">`) at the top of each report.
- Every major section wrapped in a native **`<details open><summary>…</summary>`** —
  **collapsible with no JavaScript**, which works identically in-app (`ui.html`) and in
  the **exported standalone `.html`** files (the export wrapper has no JS).
- Section anchors (`id=`) for the jump-links. Existing dark CSS, lightly enhanced:
  period tiles, green/red P&L (`.pos`/`.neg`), `<summary>` styling, tighter tables.

## New / changed pure builders (all unit-tested)

- `period_buckets(trades, today_ct, *, entry_key, exit_key)` → `{daily, weekly, mtd}`
  each `{realized, closed, wins, losses, win_rate, opened, credit}`.
- `performance_block(label, buckets, snapshot)` → the per-book period table + now-line.
- `breakdown_table(trades, key)` → grouped counts/P&L by `strategy` | `trade_type` |
  `status`.
- `toc(sections)` → the anchor jump-list. `details_section(id, title, body)` → the
  `<details>` wrapper.
- `read_snapshot()` gains `driver_paper_account` + `driver_paper_perf` reads.

## Testing

- Unit tests for every new pure builder with sample trade dicts (period boundary cases:
  a trade closed today vs last week vs last month; an open entry this week; empty book).
- Existing builders/tests stay green.
- Verified live via the preview (render `/eod` + `/eod/detail`) and by clicking
  **Generate** → opening the archived standalone files.

## Out of scope (YAGNI)

- By-symbol breakdown (user excluded it).
- Per-day historical snapshot archiving / backfill (periods derive from live trade
  dates, which is sufficient and needs no new storage).
- Charts (the report is tabular by design; the app's charts live on their own pages).
