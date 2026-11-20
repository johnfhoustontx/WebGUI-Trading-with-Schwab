# Market weather card on the homepage — design

**Date:** 2026-11-19 · **Status:** approved by the operator 2026-11-19
**Mockup:** Claude Design canvas "Market Weather Card"
(https://claude.ai/artifact/Pkoa8f4dpYx2AAfhSmn5QH — private to the operator):
the homepage with the card in place, the card in six skies, and the symbol sheet.

## Intent

A small card at the top right of neuralstrike.co, directly under the menu bar,
that tells a visitor **what kind of day the market is having, in weather terms,
within a couple of seconds** — without their needing to know what a gamma flip is.
The operator's words: *a quick read of today.*

It started as a one-off: the Market read for Thursday 2026-11-19 written up as a
weather report ("Severe storm warning: cold front sweeps all 11 sectors, fear gauge
gusts to 29"). Every line of that report mapped to a number the stack already
publishes, which is what makes it buildable: the card is a translation of the
**Market read scorecard** (`cache:market:read`), not a new analysis.

## Goals

- At a glance: a sky icon, a "temperature", a one-line headline and the seven-verdict
  tally strip. The six readings behind them are one click away.
- Current through the session: it follows the Market read's 15-minute clock.
- Honest about what it does not know: missing or stale readings show as **Fog**, and
  overnight it shows the last final reading under a moon, labelled as such.
- Looks like the site: same tokens, type and stroke style as the NeuralStrike mark.

## Non-goals

- No new analysis, data source, Schwab or proxy call. Inputs are views that exist.
- No Claude call. Code writes every word (the repo's standing rule: code states the
  facts).
- Not a forecast. Nothing on the card predicts direction.
- Not on the Live Desk or the app; homepage only. No social posting, no per-symbol
  weather.

## Anti-goals (a failure even if it "works")

- A stale sky shown as if it were current — the worst outcome for a "read of today".
- A sentence that claims more than the readings (e.g. "selloff ahead").
- Anything on the card that is not already public on `live.neuralstrike.co`.
- A site-file write that fails, raises, or delays the Market read's own publish.

## Constraints (from the code)

- **The homepage is a static file** (`deploy/site/index.html`, Caddy `file_server`).
  Its header comment pins three rules: nothing dynamic or secret, **no link to the
  app**, **no third-party origin** — the site makes zero off-origin requests
  (`deploy/tests/test_site.py`). The one sanctioned way live content reaches it is a
  generated, gitignored JSON file at the site root that a same-origin script reads:
  `ideas.json` + `assets/ideas.js` (`services/options_svc/site_ideas.py`). Caddy
  already serves root `*.json` with `Cache-Control: no-cache`
  (`deploy/caddy/generate_caddyfile.py`, the `@revalidate` matcher).
- **The Market read** is built by `services/market_svc/market_read.py` and published
  by `scheduler.refresh_read` → `handlers.publish_read` on clock slots: the first slot
  strictly after the open (08:45 CT) through the close (15:00), every
  `interval_min` (15 or 30). Seven rows in `ROW_KEYS` order — direction, breadth,
  structure, volatility, flow, cross_asset, volume — each with a verdict
  (`tailwind`/`headwind`/`neutral`/`none`) and its facts. It has an `enabled` switch
  (off publishes a retraction) and a `public` switch (`config/market_read.toml`,
  re-read every poll).
- **The regime** label ("Stressed", "Balanced", …) is `cache:sentiment:regime`
  (`label`, `committed_label`).
- **The page already ships the NYSE clock** to the browser: `assets/market-clock.js`
  is generated from `shared/market_calendar.py` (holidays, session bounds) and read
  by `assets/market-glow.js`. Script order is pinned by `test_site.py`.
- `deploy/site/live.html` links into `live.neuralstrike.co` already, so a link to the
  Live Desk from the card is within the "no link to the app" rule (the *app* is
  `app.`; `live.` is the public screens).

## Approaches considered

1. **Generated JSON + same-origin script (chosen).** `market_svc` writes
   `deploy/site/weather.json` after each Market read slot; `assets/weather.js` draws
   the card. Same shape as the trade ideas strip, keeps every site rule, costs one
   small file write per slot.
2. **Embed or fetch the public live screens** (an iframe of a `live.` page, or a
   `fetch` to a `live.` endpoint). Rejected: it breaks the zero-off-origin rule, adds
   a cross-origin dependency, and an iframe is heavy for a 400 px card.
3. **A drawn image** (a PNG from `shared/card_draw.py`, regenerated per slot).
   Rejected: text in an image is not readable by a screen reader or a search engine,
   the toggle is impossible, and it would be one more renderer to keep in step.

The operator chose the compact card with readings on request (over the full card,
and over compact-only).

## The design

### Data flow

```
market_svc scheduler.refresh_read
   └─ publish_read(reading)            (unchanged, first)
   └─ site_weather.publish(reading, regime)   NEW, never raises
          └─ deploy/site/weather.json  (atomic write: temp file + rename)
browser: index.html → assets/market-clock.js → assets/weather.js
          └─ fetch("weather.json") on load, again every 5 min while the tab is visible
```

- New module **`services/market_svc/site_weather.py`**: pure functions that turn a
  reading (plus the regime) into the card's payload, and one `publish()` that writes
  it. `publish()` catches everything and calls `_degrade.degraded("market.weather")`;
  it is called **after** `publish_read`, so a site failure never costs the reading.
  The regime is read from `cache:sentiment:regime` inside `site_weather` (absent →
  `null`, never a guess).
- The retraction and the public switch reach the file too: when the Market read is
  switched off or made non-public, `weather.json` becomes `{"v": 1, "hidden": true}`
  and the card disappears. Both paths already exist in `refresh_read`.
- `deploy/site/weather.json` and its temp-file pattern go in `.gitignore`, like
  `ideas.json` (committed, the first write would dirty prod's tree and
  `tools/promote.sh` refuses a dirty tree).

### `weather.json` (v1)

```json
{
  "v": 1,
  "date": "2026-11-19", "slot": "15:00", "ts": 1795122002,
  "final": true, "next_slot": null, "interval_min": 15,
  "sky": "storm",
  "label": "Severe storm",
  "temp": {"spx_pct": -2.01, "ndx_pct": -2.21, "tone": "cold"},
  "headline": "Cold front sweeps 35 of 37 markets; the fear gauge gusts to 28.7.",
  "tally": ["headwind","headwind","headwind","headwind","neutral","headwind","none"],
  "tally_text": "5 headwinds · 1 neutral · 1 no reading",
  "regime": "Stressed",
  "readings": [
    {"key": "temperature", "name": "Temperature", "sub": "S&P 500 · Nasdaq-100", "value": "−2.0° · −2.2°", "tone": "cold"},
    {"key": "coverage", "name": "Rain coverage", "sub": "Markets falling on our board", "value": "35 of 37", "tone": "cold"},
    {"key": "wind", "name": "Wind", "sub": "VIX, up 16.7%", "value": "28.7", "tone": "cold"},
    {"key": "pressure", "name": "Pressure", "sub": "SPY 716 vs the gamma flip 722", "value": "Below", "tone": "cold"},
    {"key": "umbrellas", "name": "Umbrellas : sunglasses", "sub": "Puts to calls in the option flow", "value": "161 : 39", "tone": "plain"},
    {"key": "shelter", "name": "Shelter", "sub": "Bonds up, high yield down", "value": "Risk-off", "tone": "cold"}
  ]
}
```

- Every string is written by the service; the script only places them. Numbers are
  formatted service-side (two decimals rule does not bind a site card; one decimal
  for a temperature and the VIX, as the mockup).
- A reading whose row is `none` is **left out** of `readings` (not shown as zero);
  the tally still shows it as an empty bar.
- `tone` is `warm` (tailwind), `cold` (headwind) or `plain` (neutral / no verdict),
  taken from that row's verdict — never re-derived from the number.
- `date`, `slot`, `ts`, `final`, `next_slot` and `interval_min` are copied from the
  reading as published. `ts` is the reading's slot stamp (unix seconds), which is
  what the browser's freshness rule measures; `final` is the reading's own
  `is_final` (so an early-close day's last slot is final). The browser never
  recomputes them.

### Where each reading comes from

Every figure is a fact already in the published reading (`cache:market:read`), which
the public Desk already shows. Nothing reads another view except the regime chip.

| Reading | Source in the reading | Notes |
|---|---|---|
| Temperature | `direction.facts.spx_pct`, `ndx_pct` | |
| Rain / sun coverage | `breadth.facts.advancing`, `declining`, `flat` | The total is **all priced tiles**, `advancing + declining + flat` — the Macro Board's equity tiles, called "markets on our board". Cold skies count `declining`, warm skies `advancing`; "all {total}" only when the counted side equals the total. *(My call.)* |
| Wind | `volatility.facts.vix_level`, `vix_pct` | |
| Pressure | `structure.facts.symbols[0]` (`spot`, `flip`) | The first symbol in `[structure] symbols` (SPY by default). The same levels are public on `/gamma`. "Below" / "Above" / "At the flip" from that symbol's state. *(My call.)* |
| Umbrellas : sunglasses | `flow.facts.puts`, `calls` | Counts of contracts in the day's flow tally, **not** volume and not any one contract's quote. When the flow row's own `public` flag is false (the operator's `[sides] public` switch), the reading is **left out** and its tally bar is drawn as no reading: the card follows that switch, never overrides it. *(My call.)* |
| Shelter | `cross_asset.facts.tiles` (`TLT`, `HYG`, `$DXY` with `pct` and `state`) | The sub-line names what the tiles did ("Bonds up, high yield down"); the value is "Risk-off" / "Risk-on" / "Mixed" from `risk_on` vs `risk_off`. |
| Regime chip | `cache:sentiment:regime` `label` | Read at each slot's write, so it can lag the regime by up to one slot. Accepted: the chip is context, and it never sets the sky except through the storm rule. *(My call.)* |

The volume row has no reading line; it appears only as its bar in the tally.

### The sky (service side)

Count `T` = tailwinds, `H` = headwinds, `R` = verdicts with a reading (not `none`).
First match wins:

| Sky | Rule (defaults; thresholds in config) |
|---|---|
| `fog` | `R < min_readings` (4) |
| `storm` | `H >= storm_headwinds` (5), **or** the regime's `committed_label` is `crisis` **and** `H > T` |
| `clear` | `T >= clear_tailwinds` (5) **and** `H == 0` |
| `fair` ("Mostly sunny") | `T > H` |
| `showers` ("Scattered showers") | `H > T` |
| `overcast` | otherwise (`T == H`) |

The regime only escalates to a storm when the verdicts already lean cold: a relief
rally inside a Stressed regime is not a storm.

### Fog and the moon (browser side)

The file cannot know whether the service has died, so the script decides freshness
with the shipped market clock:

- **Session open, file is today's and younger than `2 × interval_min`** → the sky as
  published, with a live dot and "Live · 11:15 AM CT".
- **Session open, file older than that** (or from an earlier day) → **Fog**:
  "Readings delayed", no headline, no readings. Never the old sky.
- **Session closed and the file is the most recent session's** (nights, weekends,
  holidays, and 08:30–08:45 before the first slot) → the **moon**: "After the close",
  the last final temperature, "last sky: severe storm", and "Next forecast at
  8:45 AM CT <weekday>".
- **Session closed and the file is older than the most recent session** → the card
  stays hidden. Better absent than wrong.
- When a slot's build fails, `refresh_read` retries and writes nothing, so the last
  file stays until the fog rule above takes over. That is intended: the browser,
  not the service, is what guarantees a stale sky is never shown as current.
- `hidden: true`, a missing or unparsable file, or scripting off → the card is not
  shown, and the hero's mark stays exactly as today.

### The headline

Composed in code from three parts, each chosen by rule from facts, with every number
taken from the reading:

1. **Front**, by sky: storm "Cold front sweeps", showers "Light rain falls over",
   overcast "No front either way;", fair "Sun breaks through over", clear "A warm
   front lifts".
2. **Coverage**, from breadth: "{declining} of {total} markets" (or advancing for warm
   skies); "all {total} markets" when one side is empty.
3. **Wind**, from volatility: "the fear gauge gusts to {vix}" when the VIX rose by at
   least `volatility.vix_move_pct`, "sinks to {vix}" when it fell by that much, "holds
   near {vix}" otherwise.

A missing part is dropped, not invented (no VIX → no wind clause). The full phrase
table lives in `site_weather.py` with a test per sky. No sentence may name a future
move.

### The card (browser side)

Matches the mockup's compact state:

- `<aside class="ns-weather" aria-label="Market weather" hidden>` in the hero's right
  column. When the script has a card to show it unhides the aside and hides the hero
  mark (`.ns-hero-mark`); otherwise the hero is unchanged.
- Header: "Market weather" eyebrow, live dot, time stamp.
- Sky panel: icon (84 px), label, temperature (S&P move, one degree per percent),
  "S&P 500 today".
- Headline (one sentence, about two lines).
- Tally strip: seven bars in `ROW_KEYS` order, amber / blue / grey / outlined, with
  the text tally and the regime chip.
- Footer: a real `<button aria-expanded>` "Show the readings" that unfolds the rows
  in place, and "Live Desk →" to `https://live.neuralstrike.co/desk`.
- Expanded: the readings list and a one-line footnote ("From the Market read at
  3:00 PM CT. Measured readings, not a forecast.").
- Phone width: the hero wraps and the card drops below the headline, full width up
  to 380 px.
- No `aria-live`: a card that announces itself every 15 minutes is noise.

### The symbols

The thirteen icons on the mockup's symbol sheet, as inline SVG in `weather.js` (no
icon font, no image requests): seven skies — sun, sun behind cloud, overcast, rain,
storm (cloud + amber bolt), fog, moon — on a 48 grid, and six readings —
thermometer, droplet, wind, barometer, umbrella, house — on a 24 grid. One stroke
weight, round caps and joins, `currentColor`. Warm amber `#f2b65a` means tailwind,
cold blue `#5b8fd9` / `#8fb8f2` means headwind; they also differ in lightness so they
read apart without colour.

### Configuration

A new `[weather]` table in `config/market_read.toml`, read through
`shared/market_read_config.py` (defaults in code, never raises):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Write `weather.json` at all (off writes `hidden`) |
| `min_readings` | `4` | Fewer verdicts with a reading → fog |
| `storm_headwinds` | `5` | Headwinds that make a storm on their own |
| `clear_tailwinds` | `5` | Tailwinds needed for clear skies |
| `refetch_sec` | `300` | How often the page re-reads the file (rides in the file) |

Each key gets a `webgui/config_schema.py` entry (Settings → Configuration).

## Decisions and who made them

- **Theirs:** a quick read of today; compact with readings on request; top right under
  the menu bar.
- **Mine, to check:** the sky rules and their thresholds; the regime escalating only
  when `H > T`; fog decided in the browser; the card replacing the hero mark rather
  than sitting above it; the warm/cold palette; the headline phrase table; leaving
  `none` rows out of the readings; a 5-minute re-fetch; the coverage total counting
  flat tiles; SPY for Pressure; the flow reading following its own `public` flag;
  the regime chip lagging by up to one slot.

## Left to the builder

- The exact phrase table and pluralisation, within the rules above.
- CSS: classes in `assets/site.css` against the existing tokens; whether the inline
  SVGs live in `weather.js` or a small `assets/weather-icons.js`.
- Tests. At least: each sky rule and its boundary; `none` rows dropped; `hidden` on
  each switch; `publish()` swallowing a write error with a degrade; `test_site.py`
  pins (script order after `market-clock.js`, the aside ships `hidden`, still zero
  off-origin requests). Whether the browser freshness rules get a JS test or a
  documented manual check.
- The nightly backup's file list is derived from `.gitignore`: decide whether
  `weather.json` (regenerable) is excluded from it, as the other generated site
  files are.
- Docs in the same change: CHANGELOG entry; the public-site section of the User
  Guide; the sky rules in the Technical Reference.
