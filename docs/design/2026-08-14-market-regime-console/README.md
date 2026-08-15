# Handoff: Market Regime Console Dashboard

## Overview
A single-screen, dark "console" dashboard for a market-regime detection system. It presents four
information blocks on one 1440px-wide page:

1. **Market Sentiment** — multi-timeframe sentiment score (day / week / month) + bias and model confidence.
2. **Market Trend** — multi-timeframe trend score with a qualitative verdict and trader guidance.
3. **Signals** — bias / signal / previous close / change readouts, rate-of-change + z-score meters, and a divergence alert.
4. **Market Regime** — dominant regime with confidence dial, active diagnostic tags, and regime-share
   ranking (share bars, intraday sparklines, change vs open) plus three summary callouts.

The design goal was infographic legibility: every number is paired with a shape that encodes it
(linear meter, dial, sparkline), so the page can be read at a glance from a distance.

## About the Design Files
The files in this bundle are **design references created in HTML** — a prototype showing intended
look, structure, and data encoding. They are **not production code to copy directly**.

The task is to **recreate this design in the target codebase's existing environment** (React, Vue,
Svelte, SwiftUI, native, etc.) using its established component patterns, styling solution, and
charting/primitives libraries. If no environment exists yet, choose the most appropriate framework
for the project and implement the design there.

Note on the prototype's structure: `Regime Dashboard.dc.html` is a single self-contained streaming
HTML component. All styling is **inline** and all derived values (bar widths, sparkline point
strings, the dial dash array) are computed in one JS class. In a real codebase these should become
proper components with the codebase's styling approach and a real data layer.

## Fidelity
**High-fidelity (hifi).** Colors, typography, spacing, and data encodings are final. Recreate the UI
faithfully using the codebase's existing libraries and patterns. Layout is fixed-width (1440px);
responsive behavior was not designed and is called out below as an open decision.

## Screens / Views

### Screen: Market Regime Console (single page)
**Purpose:** A trader/analyst opens this to answer, in order: *What is the market doing right now?*
*Is the trend confirming?* *What are the discrete signals?* *Which regime dominates and by how much?*

**Page frame**
- Width: `1440px` fixed. Height: content-driven (~1330–1400px).
- Padding: `32px 36px 28px`.
- Background: `radial-gradient(1200px 700px at 22% 10%, #0b1620 0%, #05070b 62%)` on a `#05070b` body.
- Root layout: vertical flex, `gap: 22px`.
- Text color: `#e7edf3`.

**Section order (top → bottom)**
1. Header bar
2. Three-card row: Sentiment | Trend | Signals — `display: grid; grid-template-columns: 1fr 1fr 1.05fr; gap: 20px; align-items: stretch`
3. Regime block — `display: grid; grid-template-columns: 396px 1fr; gap: 22px; align-items: start`
   - Left column (396px): confidence dial card, diagnostic tags card (vertical flex, `gap: 22px`)
   - Right column: regime-share table card, then a 3-up callout strip (vertical flex, `gap: 22px`)
4. Footer disclaimer bar

---

#### Component: Header bar
- Layout: flex row, `align-items: flex-end`, `justify-content: space-between`; bottom border
  `1px solid rgba(34,227,211,0.18)`, `padding-bottom: 16px`.
- Left: live dot + title + subtitle.
  - Live dot: `10×10px`, `border-radius: 50%`, `background #22e3d3`, `box-shadow: 0 0 12px #22e3d3`,
    animation `pulseDot 2.4s ease-in-out infinite` (opacity 1 → .25 → 1).
  - Title: "MARKET REGIME CONSOLE" — Rajdhani 700, `30px`, `letter-spacing: .16em`, uppercase.
  - Subtitle: "Sentiment · trend · signals · regime share" — IBM Plex Mono, `11.5px`,
    `letter-spacing: .24em`, uppercase, `#6b7d8d`, `padding-left: 24px` (aligns under title).
- Right: two meta chips, flex row `gap: 10px`.
  - Chip A "SESSION / US EQUITIES · RTH": border `1px solid rgba(120,140,160,0.22)`,
    background `rgba(12,18,26,0.7)`, padding `9px 14px`; label mono `9.5px` `.22em` `#5d6f7e`,
    value mono `13px` `#cfe0ec`.
  - Chip B "DATA AS OF / 16:30 ET · STREAMING": border `1px solid rgba(34,227,211,0.35)`,
    background `rgba(10,26,28,0.7)`; label `#4f9d98`, value `#22e3d3`.

---

#### Component: Market Sentiment card
Shell (shared by all three top cards): border `1px solid rgba(120,140,160,0.2)`, background
`linear-gradient(160deg, rgba(14,22,30,0.95), rgba(7,10,15,0.95))`, padding `20px 22px 22px`,
vertical flex `gap: 18px`, `overflow: hidden`, no border radius (hard edges are intentional).
Corner glow overlay: absolutely positioned, `radial-gradient(420px 200px at 0% 0%, rgba(53,214,138,0.10), transparent 70%)`, `pointer-events: none`.

- **Card head:** title "MARKET SENTIMENT" (Rajdhani 700, `19px`, `.16em`, uppercase) + right meta
  "SCALE 0—100" (mono `10px`, `.18em`, `#5d6f7e`).
- **Hero read:** flex row `align-items: flex-end; gap: 16px`.
  - Value `73` — IBM Plex Mono 600, `76px`, `line-height: .85`, `letter-spacing: -.02em`, color `#35d68a`.
  - Right stack: "DAY READ" (mono `10px`, `.28em`, `#6b7d8d`); below it a row `gap: 8px` with
    a pill "LONG 7.28" (border `1px solid rgba(53,214,138,0.45)`, background `rgba(12,34,25,0.8)`,
    padding `4px 10px`, mono `12px`, `.16em`, `#35d68a`) and delta text "▲ +13 vs WEEK" (mono `12px`, `#35d68a`).
- **Timeframe meters** (vertical flex `gap: 12px`). Each row: flex `gap: 12px` with
  label (mono `10px`, `.2em`, `#8fa1b0`, width `52px`) · track (flex:1, height `18px`,
  background `rgba(120,140,160,0.09)`, border `1px solid rgba(120,140,160,0.14)`, position relative) ·
  value (mono 600, `17px`, width `34px`, right-aligned).
  - Fill: absolutely positioned from left, width = value%, `linear-gradient(90deg, <color>@20-25%, <color>)`.
  - End marker: 2px wide bar at `left: <value>%`, `top:-3px; bottom:-3px`, near-white tint of the series color.
  - DAY 73 → `#35d68a` (fill glow `0 0 16px rgba(53,214,138,0.45)`, marker `#d9fff0`)
  - WEEK 60 → `#b9cf6a` (marker `#eef7cf`)
  - MONTH 53 → `#e0b74e` (marker `#f8e9c2`)
  - Ruler row: empty 52px spacer, then flex `justify-content: space-between` with 0 / 25 / 50 / 75 / 100
    (mono `9.5px`, `#4b5a67`), `border-top: 1px solid rgba(120,140,160,0.14)`, `padding-top: 5px`; 34px trailing spacer.
- **Footer block** (`margin-top: auto`, vertical flex `gap: 9px`):
  - Row: "MODEL CONFIDENCE" (mono `10px`, `.22em`, `#5d6f7e`) / "70%" (mono `13px`, `#35d68a`).
  - Segmented meter, height `10px`: track `repeating-linear-gradient(90deg, rgba(120,140,160,0.14) 0 calc(10% - 4px), transparent calc(10% - 4px) 10%)`;
    fill inset `0 30% 0 0` (= 70%) using `repeating-linear-gradient(90deg, #35d68a 0 calc(14.28% - 5.7px), transparent calc(14.28% - 5.7px) 14.28%)`,
    glow `0 0 12px rgba(53,214,138,0.35)`. (In a real codebase, prefer N discrete cells via flex + gap.)
  - Link "COMPONENTS →" — mono `11px`, `.22em`, uppercase, `#22e3d3`, `border-top: 1px solid rgba(120,140,160,0.16)`, `padding-top: 12px`.

**Copy/data used:** day 73, week 60, month 53, bias "LONG 7.28", delta "+13 vs WEEK", confidence 70%.

---

#### Component: Market Trend card
Same shell; glow overlay is `radial-gradient(420px 200px at 100% 0%, rgba(224,183,78,0.09), transparent 70%)`.
- Head: "MARKET TREND" + "SCALE 0—100".
- Hero: `68` in `#d7d76a` (same type spec as Sentiment); pill "FADING"
  (border `rgba(224,183,78,0.45)`, background `rgba(34,27,10,0.8)`, `#e0b74e`); delta "▼ −15 vs MONTH" `#e0b74e`.
- Meters (identical geometry to Sentiment):
  - DAY 68 → `#d7d76a`, glow `0 0 16px rgba(215,215,106,0.3)`, marker `#f6f6d0`.
  - WEEK → **no-data state**: track filled with `repeating-linear-gradient(135deg, rgba(120,140,160,0.10) 0 6px, transparent 6px 12px)`,
    centered label "NO READ" (mono `9.5px`, `.22em`, `#5d6f7e`), value cell shows an em dash in `#5d6f7e`.
  - MONTH 83 → `#35d68a`, marker `#d9fff0`.
  - Same 0/25/50/75/100 ruler row.
- **Verdict block** (`margin-top: auto`): `border-left: 3px solid #e0b74e`,
  background `linear-gradient(90deg, rgba(34,27,10,0.85), rgba(9,14,20,0.5))`, padding `12px 14px`, flex column `gap: 6px`.
  - Headline "LACK OF BULLISHNESS" — Rajdhani 700, `19px`, `.1em`, uppercase, `#e0b74e`.
  - Body "Buyers exhausted at highs — favor CCS, trim longs." — mono `11.5px`, `line-height: 1.55`, `#a9bac7`, `text-wrap: pretty`.
- Link "TREND DETAIL →" (same spec as COMPONENTS link).

---

#### Component: Signals card
Shell as above (no glow overlay), vertical flex `gap: 16px`.
- Head: "SIGNALS" + "4 READS · LIVE" (mono `10px`, `.18em`, `#4f9d98`).
- **Readout matrix:** `grid-template-columns: 1fr 1fr; gap: 1px`, container background
  `rgba(120,140,160,0.16)` + border `1px solid rgba(120,140,160,0.18)` — the 1px gap IS the hairline grid.
  Each cell padding `16px 16px 14px`, background `linear-gradient(150deg, <tint>, rgba(8,12,17,0.95))`:
  - **BIAS / Long** — tint `rgba(11,28,22,0.95)`; kicker mono `9.5px` `.24em` `#5d8f7c`;
    value Rajdhani 700 `34px` `.06em` `#35d68a`; sub "MARKET DIRECTION" mono `9.5px` `.16em` `#6b7d8d`.
  - **SIGNAL / Bullish** — tint `rgba(9,28,30,0.95)`; kicker `#4f9d98`; value `#22e3d3`; sub "STRENGTH & MOMENTUM".
  - **YESTERDAY / 6.02** — tint `rgba(26,21,9,0.95)`; kicker `#9b8759`; value mono 600 `32px` `#e0b74e`; sub "PREVIOUS CLOSE".
  - **CHANGE / +1.26** — tint `rgba(11,28,22,0.95)`; kicker `#5d8f7c`; value mono 600 `32px` `#35d68a`; sub "VS YESTERDAY · 6.02 → 7.28".
- **ROC / z-score meters:** section label "RATE OF CHANGE · Z-SCORE" (mono `10px`, `.24em`, `#5d6f7e`),
  then three rows (flex `gap: 12px`): label (mono `10px`, `#8fa1b0`, width `46px`) ·
  track (flex:1, height `8px`, background `rgba(120,140,160,0.10)`) · value (mono `13px`, width `46px`, right).
  Tracks are **centered/bipolar**: a 1px zero line at `left: 50%` (`rgba(160,180,195,0.5)`, `top:-3px; bottom:-3px`)
  and the fill starts at 50% and extends right by a width proportional to the value.
  - 3D ROC +1.63 → width `32%`, `#35d68a`, glow `0 0 10px rgba(53,214,138,0.4)`
  - 5D ROC +0.48 → width `10%`, `#35d68a`
  - 20D Z +1.11 → width `22%`, `#22e3d3`
  (Scale used: right half = 0 → +5 for ROC and 0 → +2.5 for z; negative values mirror leftward.)
- **Divergence alert** (`margin-top: auto`): border `1px solid rgba(242,100,107,0.3)`,
  background `rgba(30,12,15,0.6)`, padding `12px 14px`, flex row `align-items: center; gap: 16px`.
  - Left: "DIVERGENCE · LOW CONVICTION" (mono `9.5px`, `.24em`, `#f2646b`) +
    "Market Breadth 10 vs Rotation 5" (mono `11.5px`, `#d8b9bc`).
  - Right: two-bar mini chart, `align-items: flex-end; gap: 4px`, height `30px`;
    bar 1 `9×30px` `#f2646b` opacity .85 (breadth 10), bar 2 `9×15px` `#f2646b` opacity .45 (rotation 5).

---

#### Component: Regime confidence dial card (left column, 396px)
- Shell as top cards, padding `22px 24px 24px`; glow `radial-gradient(320px 220px at 50% 42%, rgba(34,227,211,0.10), transparent 70%)`.
- Kicker "REGIME IDENTIFIED" (mono `10px`, `.26em`, `#5d6f7e`).
- Dial: `244×244px` SVG, `viewBox 0 0 244 244`, whole SVG rotated `-90deg` so the arc starts at 12 o'clock.
  - Outer hairline ring: `circle r=104`, stroke `rgba(120,140,160,0.14)`, width 2.
  - Track: `circle r=92`, stroke `rgba(120,140,160,0.10)`, width 16.
  - Value arc: `circle r=92`, stroke = accent (`#22e3d3`), width 16, `stroke-linecap: butt`,
    `stroke-dasharray = (2πr × 0.56) (2πr)` → `323.7 578.1`; `filter: drop-shadow(0 0 10px rgba(34,227,211,0.55))`.
  - Inner hairline: `circle r=74`, stroke `rgba(120,140,160,0.12)`, width 1.
  - Centered stack: "WHIPSAW" (Rajdhani 700, `40px`, `.1em`, uppercase, `#e7edf3`),
    "56%" (mono 600, `46px`, `#22e3d3`), "CONFIDENCE" (mono `9.5px`, `.2em`, `#6b7d8d`).
- Stat pair below: `grid-template-columns: 1fr 1fr; gap: 1px` hairline grid (`rgba(120,140,160,0.18)`),
  cells `background #0a0e14`, padding `10px 12px`:
  - "LEAD / +10.2 pp / over Balanced"
  - "TIGHTEST TODAY / 0.2 pp / intraday minimum"
  (label mono `9.5px` `.2em` `#5d6f7e`; value mono `17px` `#e7edf3`; note mono `10px` `#6b7d8d`)

#### Component: Diagnostic tags card (left column)
- Shell, padding `18px 20px 20px`. Head row: "DIAGNOSTIC TAGS" (mono `10px`, `.26em`, `#5d6f7e`) / "6 ACTIVE" (mono `10px`, `#4f9d98`).
- Chips: flex wrap, `gap: 8px`, padding `7px 11px`, mono `11.5px`, `letter-spacing: .08em`.
  - Neutral/teal chips: border `1px solid rgba(34,227,211,0.3)`, background `rgba(12,30,32,0.7)`,
    label `#bfe9e4`, numeric `#22e3d3` — "EMA FLAT", "BALANCED PROFILE 0.53", "ADX RISING 36", "BAND-HUG 50%".
  - Warning chips: border `1px solid rgba(242,100,107,0.34)`, background `rgba(34,14,17,0.7)`,
    label `#f0c2c5`, numeric `#f2646b` — "FAILED OR BREAKS 3", "EMA WHIPSAWS 11".

#### Component: Regime share table (right column)
- Shell, padding `20px 24px 8px`.
- Head: title "REGIME SHARE" (Rajdhani 700, `20px`, `.16em`) + "BY NOTIONAL · CHANGE VS OPEN"
  (mono `10px`, `.2em`, `#5d6f7e`, `white-space: nowrap`); then a column-label row that uses the
  **same grid as the data rows** (this alignment matters): `grid-template-columns: 190px 1fr 132px 74px; gap: 26px`
  with an empty first cell, then SHARE / TODAY / CHANGE (last right-aligned).
  Head block: flex column `gap: 12px`, `padding-bottom: 10px`, `border-bottom: 1px solid rgba(120,140,160,0.16)`.
- Rows (5): `grid-template-columns: 190px 1fr 132px 74px; gap: 26px; align-items: center;`
  `padding: 15px 0`, `border-bottom: 1px solid rgba(120,140,160,0.10)`.
  1. Name cell: color chip `8×26px` (regime color, `box-shadow: 0 0 10px <color>@45%`) + name
     (Rajdhani 600, `19px`, `.1em`, uppercase) over a note line (mono `9.5px`, `.16em`, `#5d6f7e`).
  2. Share cell: value (mono 600, `20px`, width `74px`, regime color) + bar
     (height `12px`, track `rgba(120,140,160,0.10)`, border `1px solid rgba(120,140,160,0.14)`,
     fill = regime color with `0 0 14px` glow). **Bar width is normalized to the largest share
     (38.8% → 100%)** by default; an alternate "absolute" mode scales against 100%.
  3. Sparkline: inline SVG `viewBox "0 0 132 34"`, `preserveAspectRatio: none`, single `polyline`,
     `stroke-width 1.6`, `stroke-linejoin: round`, regime color, 15 points. Note the series are stored
     in **SVG y-space (0 = top)**, so lower numbers = higher value.
  4. Change: mono `15px`, right-aligned; positive `#35d68a`, negative `#f2646b`, null "—" `#5d6f7e`.
- Row data (name · note · share · change · color · series):
  - Whipsaw · CHOP · NO EDGE · 38.8% · +2.0pp · `#c3ccd6` · [30,26,29,22,25,18,21,14,17,11,13,9,12,7,9]
  - Balanced · TWO-SIDED FLOW · 28.6% · −8.4pp · `#6f86ff` · [8,10,7,9,6,14,20,17,22,21,23,22,24,23,24]
  - Trending · DIRECTIONAL PERSIST · 27.7% · +1.4pp · `#35d68a` · [26,22,25,19,21,17,20,15,18,13,16,12,14,11,12]
  - Stressed · VOL EXPANSION · 4.9% · +4.9pp · `#f2646b` · [31,30,32,29,30,27,28,24,26,22,24,20,22,19,20]
  - Breakout · DORMANT · 0.0% · — · `#f0b83c` → **zero-state**: chip/value/bar use muted `#6a5c33`,
    no glow, and the sparkline is dashed (`stroke-dasharray: 3 4`) and flat.

#### Component: Callout strip (right column)
- `grid-template-columns: 1fr 1fr 1fr; gap: 1px` hairline grid (`rgba(120,140,160,0.18)`),
  outer border `1px solid rgba(120,140,160,0.2)`; cells padding `16px 18px`,
  background `linear-gradient(160deg, rgba(14,22,30,0.95), rgba(7,10,15,0.95))`.
- Each: kicker (mono `9.5px`, `.22em`, `#5d6f7e`), headline (Rajdhani 700, `22px`, `.12em`, uppercase), note (mono `11px`, `#8fa1b0`).
  - DOMINANT / WHIPSAW (`#e7edf3`) / "38.8% share · leads by 10.2 pp"
  - BIGGEST MOVE / BALANCED (`#6f86ff`) / "−8.4 pp · share rotating out"
  - EMERGING / STRESSED (`#f2646b`) / "+4.9 pp from zero · watch"

#### Component: Footer bar
- Flex row, space-between, `border-top: 1px solid rgba(34,227,211,0.18)`, `padding-top: 14px`, mono `10.5px`, `.2em`, uppercase.
- Left `#5d6f7e`: "Whipsaw leads Balanced by 10.2 pp · tightest spread today 0.2 pp · breakout dormant".
- Right `#3f4e5b`: "For informational purposes only · not financial advice".

## Interactions & Behavior
The prototype is intentionally near-static (a read-only monitoring surface). What exists and what is expected:

**Implemented in the prototype**
- Live-status dot pulse: `@keyframes pulseDot` — opacity 1 → .25 → 1, `2.4s ease-in-out infinite`.
- Two text links: "COMPONENTS →" and "TREND DETAIL →" (hover `#22e3d3` → `#7ef7ea`). They are placeholders
  (`#components`, `#trend`) and should route to the sentiment-components breakdown and the trend-detail view.

**Expected in the real implementation**
- Data refresh: the header's "DATA AS OF ... STREAMING" implies a live feed (websocket or poll).
  On update, animate numeric changes and bar widths with a short transition (~300ms ease-out); do not re-mount rows.
- Hover on a regime row: subtle row highlight and a tooltip with the full intraday series
  (time, share%, change) — recommended, not in the prototype.
- Loading state: render the card shells and hairline grids with muted skeleton bars; keep layout stable so nothing shifts.
- Empty/no-data state: follow the two states already designed — hatched track + "NO READ" (Trend week),
  and muted + dashed flat sparkline (Breakout 0.0%).
- Stale-data state (recommended): switch the header chip B border/text to the amber `#e0b74e` family and label "STALE".
- Responsive: **not designed.** The layout is fixed at 1440px. Suggested breakpoints if needed —
  below ~1280px stack the three top cards 2 + 1; below ~1024px make everything single-column and
  collapse the regime table's sparkline column first, then the note lines.

## State Management
Data shape the UI needs (one snapshot object):

```ts
type Timeframes = { day: number | null; week: number | null; month: number | null };

type Snapshot = {
  asOf: string;              // "16:30 ET"
  session: string;           // "US EQUITIES · RTH"
  streaming: boolean;        // header chip state
  sentiment: {
    scores: Timeframes;      // 73 / 60 / 53
    bias: 'Long' | 'Short' | 'Flat';
    biasScore: number;       // 7.28
    confidence: number;      // 0.70
  };
  trend: {
    scores: Timeframes;      // 68 / null / 83
    verdict: string;         // "Lack of Bullishness"
    guidance: string;        // "Buyers exhausted at highs — favor CCS, trim longs."
  };
  signals: {
    bias: string;            // "Long"
    signal: string;          // "Bullish"
    previousClose: number;   // 6.02
    change: number;          // +1.26
    roc3d: number; roc5d: number; z20d: number;
    divergence?: { breadth: number; rotation: number; note: string };
  };
  regime: {
    name: string;            // "Whipsaw"
    confidence: number;      // 0.56
    tags: { label: string; value?: string; severity: 'info' | 'warn' }[];
    shares: { name: string; note: string; share: number; changePp: number | null; series: number[] }[];
  };
};
```

Derived values (compute in a selector/hook, not in markup):
- `barWidth = share / max(shares) * 100` (relative mode) or `share` (absolute mode).
- Sparkline points: `series.map((v,i) => [i * (width/(n-1)), v])` — series already in y-down space; if you
  switch to value-space, invert with `y = height - (v - min)/(max - min) * height`.
- Dial arc: `strokeDasharray = `${2πr * confidence} ${2πr}`` with the ring rotated −90°.
- Deltas: "+13 vs WEEK" = `day - week`; "−15 vs MONTH" = `day - month`. Color/arrow follow the sign.
- Zero/no-data states are derived, not authored: `share === 0` → muted + dashed; `score == null` → hatched "NO READ".

Two prototype-level options exist as props and are worth keeping as configuration:
- `accentColor` — dial/accent hue (`#22e3d3` default; `#6f86ff`, `#c86bff`, `#35d68a` offered).
- `barScale` — `"relative"` (default) or `"absolute"` for the regime-share bars.

## Design Tokens

**Surfaces & lines**
| Token | Value | Use |
|---|---|---|
| bg/base | `#05070b` | page background |
| bg/glow | `radial-gradient(1200px 700px at 22% 10%, #0b1620, #05070b 62%)` | page wash |
| surface/card | `linear-gradient(160deg, rgba(14,22,30,.95), rgba(7,10,15,.95))` | all cards |
| surface/cell | `#0a0e14` | stat cells |
| line/hairline | `rgba(120,140,160,0.18)` | grid gaps, cell borders |
| line/border | `rgba(120,140,160,0.2)` | card borders |
| line/accent | `rgba(34,227,211,0.18)` | header/footer rules |
| track | `rgba(120,140,160,0.09–0.10)` | meter tracks |

**Text**
| Token | Value |
|---|---|
| text/primary | `#e7edf3` |
| text/secondary | `#a9bac7` |
| text/muted | `#8fa1b0` |
| text/label | `#6b7d8d` |
| text/dim | `#5d6f7e` |
| text/faint | `#4b5a67` / `#3f4e5b` |

**Data colors**
| Token | Value | Meaning |
|---|---|---|
| accent/teal | `#22e3d3` | primary accent, signal, 20d z |
| pos/green | `#35d68a` | bullish, positive change, high scores |
| mid/olive | `#b9cf6a` | mid-high score (week 60) |
| mid/yellow | `#d7d76a` | trend day 68 |
| warn/amber | `#e0b74e` | caution, previous close, month 53 |
| neg/red | `#f2646b` | stressed, negative change, divergence |
| regime/whipsaw | `#c3ccd6` | neutral grey series |
| regime/balanced | `#6f86ff` | blue series |
| regime/breakout | `#f0b83c` (muted `#6a5c33`) | amber series / zero state |

**Typography** — Rajdhani (500/600/700) for display & names; IBM Plex Mono (400/500/600) for all numerics,
labels, and micro-copy. Scale: `76` hero · `46/40` dial · `34/32` readouts · `22/20/19` headings ·
`17/15/13` values · `11.5/11/10/9.5` labels. Uppercase labels carry `letter-spacing` `.16–.28em`;
display headings `.06–.16em`. Substitute with the codebase's condensed-display + mono pair if these aren't available.

**Spacing** — 1 / 4 / 6 / 8 / 10 / 12 / 14 / 16 / 18 / 20 / 22 / 26 / 32 / 36 px. The recurring `1px` is
deliberate: hairline grids are built with `gap: 1px` over a light background rather than borders.

**Radius / shadow** — `border-radius: 0` everywhere (hard-edge console look).
Shadows are **glows only**: `0 0 10–16px <color> @ 30–50%` on active fills, chips, and dots;
plus `drop-shadow(0 0 10px rgba(34,227,211,.55))` on the dial arc. No elevation shadows.

## Assets
No images, icon fonts, or external assets. All visuals are CSS gradients or inline SVG
(dial rings + sparkline polylines). Fonts are loaded from Google Fonts
(`Rajdhani` 500/600/700, `IBM Plex Mono` 400/500/600) — self-host or swap for the codebase's
equivalents. Arrow/caret glyphs are plain text characters (`▲`, `▼`, `→`, `—`); replace with the
codebase's icon set if one exists.

## Screenshots
- `screenshots/full-dashboard.png` — the complete dashboard as rendered at 1440px width. Use it as the
  visual source of truth alongside the measurements above.

## Files
- `Regime Dashboard.dc.html` — the complete design. Single file: markup with inline styles, plus one
  JS class at the bottom that supplies the regime-row data and the computed bar widths, sparkline
  point strings, and dial dash array. Open it directly in a browser to view the design at 1440px.

## Source references
The design was developed from four reference images the user provided (three regime-infographic
explorations and one compact regime panel showing the share/sparkline/change data). Those numbers —
regime shares, diagnostic tags, sentiment/trend scores, and signal readouts — are the real data
this dashboard was built around; treat them as sample data, not fixtures.
