# Two-decimal display — design

**Date:** 2026-10-04
**Request:** every price, ratio and similar reading in the app shows exactly two
digits after the decimal point. A symbol whose price is a whole number shows as
`450.00`, not `450`.

## What was wrong

Two separate causes produced a `450`:

1. **Tables sent raw numbers.** The Matrix `Price` column, the Scanner `Credit`
   and `Max loss` columns, the Portfolio tables and others put the number itself
   in the row. Quasar prints a JavaScript number as-is: `450`, `1.5`, `0.3`.
2. **About 200 hand-written format strings across ~55 page modules.** `:g` drops
   trailing zeros (`spot 450`, `Sell 450 P`, `Gamma flip 450`); `.1f` and `.0f`
   were used for ratios, percentages and dollar totals.

## Scope (the user's choice)

| Family | Before | After |
|---|---|---|
| Prices and levels (spot, bid/ask/mark, credit, entry/stop/target, flip, walls) | `450`, `1.5` | `450.00`, `1.50` |
| Strikes | `450/445`, `Sell 450 P` | `450.00/445.00`, `Sell 450.00 P` |
| Ratios (put/call, volume/OI, IV/HV, multiples) | `1.5×` | `1.50×` |
| Percentages | `1.2%`, `65%` | `1.20%`, `65.00%` |
| Dollar totals | `$1,250`, `$1.2M`, `$45K` | `$1,250.00`, `$1.20M`, `$45.00K` |
| Scores, ranks, counts, DTE, quantity, volume, open interest | unchanged | unchanged |

Offered and declined: scores and ranks (composite score, Vol Rank, the sentiment
gauges, percentiles) stay as they are.

## Approach

- **Named formatters in `webgui/pages/fmt.py`** — `price`, `strike`, `ratio`,
  `pct`, `money`, `money_short` — all reading one `DECIMALS` constant, all
  returning `NO_READING` for an absent reading. A page calls one of them rather
  than writing a format string.
- **One table option in the page kit** — `kit.table(..., decimals=(...))`
  attaches a Quasar `:format` to the named columns. The row keeps the NUMBER, so
  a column sort stays numeric. Formatting the cell to text in Python was
  rejected: `"1,000.00"` then sorts below `"999.00"`, and `kit.page_of` sorts on
  the field's value.
- **Charts** — price and strike axes, tooltips and level labels take two
  decimals through the Highcharts format strings.
- **A guard test** fails when a page module gains a new `:g` format, the one
  that printed a whole-number price without its decimals.

`DECIMALS` is a constant, not a `config/*.toml` value: it is a unit convention,
and the JavaScript and Highcharts format strings have to follow it.

## Left alone, on purpose

- **Greeks.** Gamma and theta need more than two decimals; a 0.004 gamma would
  print `0.00`.
- **Spoken alerts** (`voice.py`). Two decimals read aloud are worse to hear.
- **Settings → Configuration.** It echoes the value the operator typed.
- **Layout numbers** that happen to be percentages: bar widths, SVG coordinates.
- **The pushed Telegram/Discord images, X posts and the daily reports.** Separate
  renderers, not app screens. The public live screens render the same page
  modules, so they change with the app.

## Verification

The webgui suite (tests that pinned the old strings are updated to the new
format, never loosened), then the local page harness for the Matrix, Scanner,
Gamma and Paper pages.
