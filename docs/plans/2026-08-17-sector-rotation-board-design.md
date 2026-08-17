# Sector Rotation — board redesign (2026-08-17)

Rebuild of `/sentiment/rotation` from a supplied design (`Sector Rotation.dc.html`,
same design project as the [Sector & Industry heat grid](2026-08-17-sector-heat-grid-design.md)
shipped the same day). The two screens are one design family: same two faces,
same near-black ground, same warm-neutral ladder.

**It is a pure Tier-1 re-render.** Every number the design shows was already in
`cache:sentiment:rotation`; `sentiment_svc` was not touched. Checked before
building — the design was clearly authored against this exact payload:

| design | live cache |
|---|---|
| `42.4% rotating out · 2 sectors` | `rotating_from` = XLY 9.94 + XLK 32.53 = **42.47%** |
| `57.5% rotating in · 9 sectors` | `rotating_into` = 9 sectors = **57.52%** |
| Improving `25.0%` · Leading `32.5%` · Lagging `9.9%` · Weakening `32.5%` | **25.02 / 32.50 / 9.94 / 32.53%** |
| `cyclical 100.49 vs 102.00 defensive` | `headline.cyclical_mom_mean` / `defensive_mom_mean` |
| `−1.51`, `threshold ±1.50` | `headline.spread`, `risk_threshold` |

## What changed

| | Before | After |
|---|---|---|
| Verdict | a coloured headline sentence + a parenthesised spread | three-panel strip: regime · **diverging gauge** · spread + trigger sentence |
| Spread | a number inside prose | a gauge on a −3…+3 track with both ±triggers and zero marked |
| Weight | a `%` column in two name lists | a **flow band** where segment area *is* index share |
| Quadrants | a 6-column table sorted by RS-Momentum | **four panels**, each with its share of the index and a chip per sector |
| RS-Ratio / Dir | table columns | **gone** (the axis rails state the RS-Ratio direction) |
| Rotating from/into | two name lists | the two halves of the flow band |

## The decisions worth recording

**The gauge fill spans between the reading and zero, not from the left.** The
spread is signed, and which side of zero it sits on *is* the verdict. A bar
growing from the left edge would encode −3 and +3 as "small" and "large" rather
than "opposite" — the one thing this chart exists to say.

**The flow band's two wrappers are themselves weight-grown.** Growing only the
segments inside each side would make both halves fill half the width regardless
of their totals, so a 42/58 split would read as 50/50. Both the wrappers and the
segments carry `flex-grow`, so the band is to scale end to end.

**The band splits on `direction`, not on quadrant.** They coincide in the current
data (Improving+Leading = INTO, Lagging+Weakening = FROM) but the engine sets
`direction` deliberately, and keying on it means the band always partitions
exactly what the assessment *called* rotating.

**Chip bars share one scale across all four panels** — the heaviest sector on the
page. Per-panel scaling would let a 2% sector alone in a quadrant draw the same
bar as a 32% one, which is exactly backwards for a chart about how much of the
index is moving.

**Every quadrant panel renders even when empty.** A quadrant nobody is in is
information ("nothing is improving"), and dropping the panel would silently
reflow the other three into its space as though the grid were simply smaller.

**Hairlines are gaps, not borders.** The verdict strip is a flex-wrap row with a
1px gap and a 1px ring per panel; the quadrant grid is a 2px gap over a coloured
ground. Real borders orphan a rule when a panel wraps or the grid reflows — the
design's own notes call this out, and it is why neither region uses `border`
between cells.

## Three things I added that the design left implicit

1. **The trigger sentence is derived, and its ladder is a judgement call.** The
   design shows one string ("Just past the trigger…"). A board that renders that
   at −1.51 must say something else at −4.0. Shipped: inside the band → "no
   rotation signal has fired"; past it but under **1.5×** → "just past… a fresh
   signal"; beyond → "well past… an entrenched rotation". The 1.5× is stated in
   `rotation_view.ENTRENCHED_RATIO` rather than buried.
2. **The verdict sentence is rewritten, not echoed.** The service's `text` is a
   log line — `"Risk-OFF rotation - money rotating into defensives, out of
   cyclicals"` — repeating the regime already shown beside it, with an ASCII
   hyphen for a dash. The two known regimes get clean prose; anything else falls
   back to the service text, so a new regime can never render as silence.
3. **The old summary content that had nowhere to go was checked, not dropped by
   accident.** `RS-Ratio` and `Dir` are genuinely gone (the axis rails carry the
   RS-Ratio reading direction; `Dir` is what the band's two sides encode). The
   `pairs` field was already unused by the page.

## Structure

- **`webgui/pages/rotation_view.py`** — all of it pure: the quadrant palette, the
  warm-neutral ladder, gauge geometry, flow sides, quadrant panels, formatting,
  derived prose. 38 tests in `webgui/tests/test_rotation_view.py`.
- **`webgui/pages/sentiment_rotation.py`** — `render()` replaced; **every existing
  builder kept**, because `pages/sentiment_rrg.py` imports `SENT_TEXT_CLASSES`,
  `headline_parts`, `regime_text_class` and `rrg_scatter_figure` from it and its
  own tests pin them.
- **`webgui/pages/oklch.py`** — the oklch→sRGB conversion, factored out of
  `sector_heat.py` now that two pages need it.
- **`config/theme.toml [rotation]`** — the two grounds and the two faces, nothing
  else. The neutral ladder and the quadrant hues are ramps, so they are code.
- **No `ui.add_css`.** The gauge is absolutely-positioned runtime percentage
  arbitraries — the documented continuous-value exception to the palette rule.

## ⚠ Open question: two quadrant palettes now coexist

`sentiment_rotation.quadrant_color` (Leading `#66bb6a` / Improving `#3fb6c7`
cyan / Weakening `#ffd54f` yellow / Lagging `#ef5350`) still drives the **RRG
scatter** and the **Sector & Industry** quadrant text. This design re-hues them
— Improving becomes **blue 232**, Weakening **olive 80** — and that is
implemented for this page only.

Two tabs in the same nav group therefore colour "Improving" differently. Green
and red are close enough between the two palettes not to clash; blue-vs-cyan and
olive-vs-yellow are visibly different. Left page-scoped deliberately: the design
covers this screen, and restyling the RRG chart is a change nobody asked for.
Worth resolving one way or the other.

## Verified live (2026-08-17)

Against prod's `cache:sentiment:rotation` copied into the dev Redis DB, at 9500:

- Gauge geometry exact: track 0–100%, triggers at **25.00 / 75.00%**, zero at
  **50.00%**, fill `24.67% → 50%` for the live spread of −1.52, value marker
  centred on 24.67%.
- Flow band: sides at **42.1% / 57.0%** of the width; 8 segments labelled and 3
  (XLB, XLRE, XLU) correctly unlabelled by the 7.5% gate; segment hues match
  quadrant (XLK olive, XLY red, XLF/XLI blue, XLC/XLV/XLE/XLP green).
- Quadrant grid: 4 panels, 2-column auto-fit; 11 chip bars, widest exactly
  **100%** (XLK) and XLF at **41.3%** = 13.42/32.53.
- Instrument Sans + JetBrains Mono both load; no page-level horizontal overflow;
  socket live on websocket transport.
- **RRG tab unaffected** — chart renders 11 series with all ETF labels.
