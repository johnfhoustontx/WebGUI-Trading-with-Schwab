# Strategy Finder redesign — top picks, a slim list, chips and presets

**Date:** 2026-09-13 · **Status:** approved design · **Route:** `/options/swing`
**Follows:** `2026-09-13-strategy-finder-all-structures-design.md` (the 13 structures).

## Request

"Make the Strategy Finder page more visually appealing and easy to use." The
operator uses the page for all three jobs equally: finding the best trade fast,
comparing structures, and exploring settings.

## What was wrong (seen in the local harness, 1440 px, a 16-row SPY scan)

- **The deciding columns were off-screen.** 14 columns forced a sideways scroll to
  reach Score, Grade and the Calculator / Paper / Expected Move buttons.
- **Half the screen was empty.** The results sat in a short ~10-row scroll box.
- **Raw numbers.** `-54057.72 debit` beside `-195.34 debit`, no currency or scale —
  a $54,000 collar read like a $196 butterfly.
- **Controls were a flat form strip:** seven small checkboxes, three number boxes and
  a collapsed delta panel, with nothing saying what matters.
- **Market view and scan status were faint one-line captions.**
- **Vol Rank repeated the same value on every row** (one symbol per scan).
- **The detail panel squeezed the table** permanently, empty until a click.

## Operator decisions

| Question | Choice |
|---|---|
| Layout | **Top picks + full list** |
| Visual extras | **Payoff shape on each card and row**, **risk / reward / odds bars** (no large chart, not numbers-only) |
| Controls | **Strategy chips**, **expiry presets**, **risk style picker** (no automatic rescan) |
| Chip behaviour | **Instant filters** — every scan builds all seven groups; chips show/hide results without rescanning |

## Page, top to bottom

1. **Scan bar.** Symbol + Scan (Enter still scans). **Expiry presets** — *1–2 wk ·
   2–6 wk · 1–3 mo · Any* — beside DTE min/max; editing a box deselects the
   preset. **Risk style** — *Conservative · Balanced · Aggressive* — sets the
   short-leg delta bands on both sides to 0.05–0.10 / **0.10–0.20 (default)** / 0.20–0.30
   (*revised while planning:* the first draft's 0.10–0.15 / 0.15–0.25 / 0.25–0.35 would
   have relabelled today's default band as *Custom* and silently changed the default
   scan); the raw
   delta and credit fields stay under **Advanced**, and editing them shows *Custom*.
2. **Summary strip** (after a scan): symbol and price; the market view as pills
   (direction · conviction · volatility regime); Vol Rank (moved out of the table);
   one count line — *16 ideas · 6 below the quality bar · 0 where premium is too
   cheap to sell*.
3. **Strategy chips** — one per group with its count, plus *All*. Filters cards and
   list instantly.
4. **Top picks** — up to four cards: the best-scoring idea from four *different*
   groups among the visible rows. Each card: name, score badge + grade, expiry ·
   DTE, legs in plain words, a payoff shape, a split risk/reward bar, a
   probability-of-profit bar, cost, and Calculator (+ Paper where allowed).
5. **Ranked list** — Strategy (with mini payoff shape) · Score · Expiry·DTE · Cost ·
   Max profit · Max loss · Probability of profit (bar) · Grade · actions. Legs,
   breakevens and bias move to the detail panel. The list grows with the page.
6. **Detail panel** — the existing shared panel, collapsed until a card or row is
   clicked, so the list keeps its width.

**Loading / empty.** While scanning, the cards show grey placeholders reading
*Scanning SPY…* instead of the previous symbol. Before any scan, one line says what
to do. A cold service shows the shared waiting line.

**Narrow screens.** Cards wrap to two columns, then one; the list scrolls sideways
inside its own box, never the page.

## Visual language

Stays inside the app's dark-navy tokens (`theme.py`) and the Tailwind-only rule.
No new palette section.

- **Payoff shape** — a hand-drawn SVG polyline at a **fixed pixel size**
  (≈120×32; ≈72×20 in the list), profitable part green, losing part red, faint zero
  line, a tick at today's price. Fixed size because a stretched `viewBox` needs
  `vector-effect`, which DOMPurify strips (CLAUDE.md gotcha). Pinned by the
  DOMPurify allow-list test the other SVG builders carry.
- **Bars.** A single scale across rows fails: a covered call's ~$53,000 max loss
  would flatten every other bar. So each idea gets **one split bar** — red max loss
  left, green max profit right, both scaled to the larger of the two, which shows the
  *shape* of the bet (a butterfly mostly green, a covered call mostly red) — and **one
  probability-of-profit bar** (0–100 %; amber < 40, neutral 40–60, green > 60).
  Unlimited profit draws a full green half marked ∞. Widths snap to **5 % steps**, a
  fixed class set, per the finite-palette rule.
- **Formatting** — `$54,058`, credit/debit as a word, whole percents, `Oct 16`; a row
  holding shares says *for 100 shares*.

## Data (options_svc, additive)

- **`payoff_curve`** on each candidate: ~25 `[price, pnl_per_contract]` points across
  spot ± 2 × the expected move to that candidate's front expiry, valued exactly as
  `payoff_metrics` values the position (intrinsic for single-expiry options,
  front-expiry Black-Scholes for later legs, spot for shares), gross of commission.
  Tier 1 cannot import the pricing engine, so the page cannot compute it for
  calendars and share structures itself.
- **`group`** on each candidate: which of the seven build groups produced it
  (`DIRECTIONAL`, `VERTICAL`, `NEUTRAL`, `STRADDLE`, `BUTTERFLY`, `CALENDAR`,
  `STOCK`). Distinct from the scoring `family` field. Stamping it in the service
  avoids a type→group mirror in Tier 1.
- **The page stops sending `families`.** The service's `None` default already builds
  all seven. `income_scan` passes its own families and is unaffected.

No contract change: `cache:options:swing` has no row contract (confirmed in the final
review of the 13-structure work).

## Code shape

- `strategy_scanner.payoff_curve(...)`, a pure function; `compute.swing_scan` stamps
  `group` as each builder returns and attaches `payoff_curve` to the emitted rows
  only — the builders themselves are untouched, so their outputs stay byte-identical.
- New PURE `webgui/pages/options/finder_view.py`: money/date formatting, expiry and
  risk-style presets (incl. *Custom* detection), chip counts and filtering, top-pick
  selection, bar geometry, payoff SVG, summary-strip facts. Unit-tested without a
  browser.
- `swing.py` becomes widgets and wiring only. New `finder_columns()` for the slim
  list; `strategy_table.strategy_columns()` is **unchanged** — the Market Scanner's
  Directional tab still uses it.
- Unchanged: the shared detail panel, the Calculator / Paper / Expected Move
  hand-offs, the `_PAPER_TYPES` gate.

## Testing

Pure-module unit tests; DOMPurify allow-list for the SVG; `test_no_inline_style.py`;
the existing `test_options_swing.py` and `test_strategy_table.py`; service tests for
`payoff_curve` and `group`; before/after screenshots from the local harness.

## Out of scope

A large payoff chart in the detail panel; automatic rescans; any change to how
structures are built, priced or scored; the Market Scanner's Directional tab layout;
persisting chip / preset choices across navigation.
