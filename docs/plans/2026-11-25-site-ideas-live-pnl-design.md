# Site trade ideas: open P&L from the Ledger's live marks (design, 2026-11-25)

The operator asked for the Open trade prices on the website's Trade Ideas to
update along with the Paper Ledger's.

## What existed

| Page | Open idea's P&L from | Moves every |
|---|---|---|
| Paper Ledger (private) | `cache:options:paper_live` (`ledger_live`, the option stream) | 3 s in the regular session |
| Site: home strip, `ideas.html`, `/t/<id>/` | the Ledger row's `last_mark_pnl` (`idea_ledger.mark_cycle`) | written every 5 min, read into `ideas.json` every `[site] refresh_min` (15 min); the page read it once per load |

So the two pages could differ by up to twenty minutes of price.

## The shape

1. **The same figure.** `site_ideas.with_live_marks(rows, marks, as_of)` (pure)
   gives each OPEN Ledger row the live mark as its `last_mark_pnl` (the view's
   TOTAL ÷ quantity, the per-contract basis) and `last_mark_at` = the view's
   `:ts`. `ledger_result` then reads it exactly as before. Both the full refresh
   and the new pass read the rows through `handlers._site_ledger_rows`.
2. **Fresh or not at all.** `ledger_live.publish` uses `skip_unchanged`, which
   still refreshes `:ts` on every evaluation (3 s), so `:ts` stops only when the
   evaluator does. A view older than `SITE_LIVE_MAX_AGE_SEC` (60) is ignored and
   the stored 5-minute mark is shown. Outside the session the view is empty.
3. **A light pass.** `site_ideas.refresh_ledger` re-reads the Ledger and the
   marks and rewrites `ideas.json` and each `t/<id>/trade.json` where a result
   moved. It fetches no quote, candle or close, so it can run every
   `[site] live_sec` (30 s default; the scheduler ticks every 30 s; 0 = off),
   gated on the regular session (`scheduler.site_live_due`), skipped on a tick the
   full refresh runs.
4. **The page follows.** Both files carry `live_sec`. `ideas.js` re-reads the file
   at that cadence (at least 15 s, not while the tab is hidden) while any trade on
   the page is open, and replaces each card's result line and badge in place,
   found by `data-full`: the image is never rebuilt. New posts appear on the next
   load. `*.json` is `no-cache` in Caddy, so a re-read is a 304 when nothing moved.

## Kept as it was

* **Only the displayed figure is live.** The stops, targets and the closed result
  are `mark_cycle`'s, on the row: the weekly review, `/eod` and the site's record
  measure ideas on that five-minute basis, and a live stop would change the
  measurement without anyone deciding to (the same reason `ledger_live` leaves
  self-managed exits alone).
* **The quotes switch.** A single-option idea's figure is still withheld while
  `show_leg_quotes` is off; `ledger_result` applies it to the live figure too.
* **The stock price** beside the result still comes from the full refresh's one
  batched quote call. A live stock price would be a new Schwab call every 30 s.
