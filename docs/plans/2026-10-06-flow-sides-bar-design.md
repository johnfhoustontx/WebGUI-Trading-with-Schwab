# Flow Alerts: the bought / sold estimate as a bar — design

**Date:** 2026-10-06. **Status:** approved by the operator the same day, from mockups.

## The request

The estimate was two lines of text in the Flow Alerts table:

> ≈ bought 47.92% · sold 34.31% · unlabelled 17.76%
> since the alert: bought 40.06% · sold 56.99% · unlabelled 2.95% of 1,016

The operator asked for coloured boxes in place of the words (green for buy, red for
sell, neutral for unknown) and was open to other suggestions. Three mockups later the
decision was:

| Round | Shown | Answer |
|---|---|---|
| 1 | Boxes; boxes plus a thin proportion bar; one verdict box plus a bar | none as drawn |
| 2 | "Make the bar wider with the text inside and eliminate the boxes" | one wide segmented bar, figures inside |
| 3 | The wide bar at 340 px / 12 px type | "slightly smaller", with a proportionally smaller font |

## What is drawn

One bar per figure, split in proportion into three segments in a fixed order:
**Buy** (green), **Sell** (red), **Unknown** (grey). 300 px wide, 20 px tall, 11 px
type on the Flow Alerts page.

- A segment wide enough prints its word and share (`Buy 47.92%`); a narrower one the
  share alone, its colour saying which it is; a sliver prints nothing.
- **A sliver's share is printed after the bar** (`Unknown 2.95%`). The estimate's one
  standing rule is that the share nobody could label is always shown, so a reading
  never looks better measured than it was. A bar with text only where it fits would
  have broken that for exactly the small shares.
- A part with no volume has no segment and no text.
- The since-alert bar ends with the contracts it covers (`of 1,016`).
- Each bar has a label: `≈ Session` and `≈ Since alert`. The `≈` is the estimate mark;
  it used to open the text.
- Hovering a bar shows the old sentence, all three shares in words.

The word on screen is **Unknown**, the operator's. The service, the payload keys and
the technical manuals keep `unlabelled`; the User Guide says once that they are the
same thing.

### Green and red

In this table green and red already mean call and put (the Side column, coloured
text). On the bar they mean bought and sold, so a put that was mostly bought is a red
"Put" beside a green bar. The bar's segments are filled blocks and the Side column is
coloured text, which keeps the two apart; the help says so in words.

## Where

| Surface | What changes |
|---|---|
| Flow Alerts table | the two bars, stacked, in the **Bought / sold (estimate)** column |
| Previous session panel | one bar per row, marked `≈` |
| The Desk's flow panel | the same bar, 190 px / 16 px / 10 px type, under the detail line |

**The Desk keeps its two-line rows.** Its first build stacked both bars and their side
text and a row stood five lines tall (measured on the page harness), on the panel
whose point is how many rows it carries. The estimate is now ONE line: the row is one
bar tall, wraps, and hides what wrapped, so a piece that does not fit is left off
whole — first the text beside a bar, then the since-alert bar — rather than cut
mid-figure. At the panel's usual width that is the session bar alone, which is also
all the old truncated text line showed there. The hover has every figure.

## How

`flow.sides_bar(share, *, chars, volume)` is the one builder, pure, and both pages
draw from it. `share` is `flow.shares()`' result.

- **Tone** is a fixed class per part (a finite map, `_BAR_PARTS`). **Width** is one
  arbitrary Tailwind class built from the share (`w-[47.92%]`), the documented
  continuous-value exception the sector and momentum bars already use. No inline
  style, in the slot or anywhere.
- **What fits** is decided in Python by a character count: a text fits when its
  length plus one is within `share × chars`. `chars` is how many characters of the
  bar's font cross the whole bar: 46 for the Flow bar (measured: a figure is 40 px,
  "Unknown 33.36%" 90 px), 32 for the Desk's. The counts follow the bar's pixel
  width and font size, so they are constants beside those classes, not config: a
  change needs the classes to follow it.
- `alert_rows` stamps `sides_bar` / `sides_after_bar` (for the table slot),
  `sides_shares` / `sides_after_shares` (the Desk builds its own narrower bar from
  them) and `sides_bought`. The words (`sides`, `sides_after`) stay, as the hover.
- **The column sorts by the bought share**, a number. The text it replaced sorted as
  a string.

### The wrap, and its floor

The bar and the text beside it sit in a wrapping row inside a non-wrapping one, so
the text drops under the bar's own left edge, never under the label. That inner row
has a floor of 460 px (the bar, the gap, 152 px). With real summaries the table is
wider than the page and scrolls sideways (1,905 px in a 1,621 px frame on the
harness at 1,700 px), and an overflowing table gives a wrappable cell only its
minimum. With no floor that minimum was the bar alone: every side text dropped a
line and a row with two bars stood four lines tall. 152 px holds the usual text
(`Unknown 10.00% · of 12,400`); two slivers and a volume still wrap. The column is
562 px, against about 490 px for the text it replaced.

## Not changed

- The estimate itself, its views, its public flag, and the service.
- The pushed image and the Telegram text (a separate renderer).
- The HIRO rows' own `% unlabelled` clause in **What traded**: a different figure in
  a different column, left in the service's word.

## Verified

On the page harness (`tools/ui_harness.py`, fake bus, seeded views): the Flow Alerts
table and the Previous session panel at 1,700 px and at the pane's width, the Desk's
flow panel at 1,280 px; no segment's text is clipped; the hover works on both pages;
no console errors.
