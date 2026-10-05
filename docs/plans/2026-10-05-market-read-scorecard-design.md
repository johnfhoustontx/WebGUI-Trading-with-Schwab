# Market read: an agreement scorecard on the Desk — design

**Date:** 2026-10-05
**Status:** approved 2026-10-05, not built.
**Plan:** `docs/plans/2026-10-05-market-read-scorecard-plan.md`.

## The question

The Desk, the Market Dashboard and Flow Alerts each answer part of "what is the
market doing". Reading the three together is what produces an inference: the tape
is up, the structure caps it, the flow does not confirm it. Today that reading is
done by eye. This section does it by rule, every 15 minutes, and shows which
readings help stocks and which hold them back.

## Decisions the operator made

- **Layout B, the agreement scorecard**: one row per question, no generated
  sentence. (A verdict-and-pillars layout and an intraday timeline were the other
  two; the timeline may follow, and the payload keeps the history it would read.)
- **The chip words are Tailwind, Headwind and Neutral**, under a column headed
  "For stocks". They are ABSOLUTE, not relative to the day's move: "supports the
  move" would be green on a day stocks fall.
- **A fourth state, No reading**, for a row whose inputs are missing or stale. A
  missing reading is never shown as Neutral.
- **Every 15 minutes in the regular session**, configurable. On the Desk, full
  width above the Market Summary, and on the public Desk behind a switch.

## What it is not

- Not a forecast and not advice. Each chip restates a reading already on a page.
- No Claude call and no Schwab call: every input is a view a service already
  publishes.
- Not the app's Bias or Signal. Those come from the sentiment composite. This is a
  count of six separate readings, and the words were chosen so the two cannot be
  mistaken for each other.

## The rows

Each row has a verdict code (`tailwind`, `headwind`, `neutral`, `none`) and the
numbers it was decided from. The service decides the code; the page formats the
numbers and maps the code to a word and a fixed colour class.

| Row | Reads | Tailwind | Headwind |
|---|---|---|---|
| Direction | `market:dashboard` tiles SPX and NDX, day change | both ≥ +`move_pct` | both ≤ −`move_pct` |
| Breadth | `market:dashboard`, advancers and decliners over the equity frames (the Macro Board's own count) | advancing share ≥ `strong_share` | ≤ `weak_share` |
| Structure | `options:matrix` rows SPY and QQQ: spot, flip, call wall, dealer mode | both above the flip with ≥ `room_pct` to the ceiling | both within `near_pct` of the ceiling in long gamma, or both below the flip |
| Volatility | `market:dashboard` tiles VIX, VIX1D, VIX3M | VIX ≤ −`vix_move_pct` on the day and below VIX3M | VIX ≥ +`vix_move_pct` on a day SPX is up, or VIX1D above VIX |
| Flow | `options:flow_sides` joined to `options:flow_alerts` for each contract's side | calls net bought by ≥ `lean_pts`, puts not | puts net bought by ≥ `lean_pts`, calls not |
| Cross-asset | `market:dashboard` tiles TLT, $DXY, HYG, by the board's own risk-on / risk-off colour state | at least two risk-on | at least two risk-off |

Rules that hold for every row:

- **In between is Neutral. Missing is No reading.** A tile with no price, a matrix
  row with no flip, a view from another day, a view older than `stale_after_sec`:
  each makes its row `none`. (The NaN rule in `CLAUDE.md`: absence is not a
  reading.)
- **Two kinds of "too old".** The dashboard and the matrix are republished on a
  clock, so an old one means its publisher has stopped: they are judged by AGE
  (`stale_after_sec`, from the view's `:ts` side key). The two flow views are
  published only when something changes, so a quiet tape leaves them legitimately
  old: they are judged by the session DATE they carry.
- **Structure distrusts walls the dealer panel distrusts.** A net gamma of exactly
  zero is the after-hours artefact; that symbol has no reading.
- **Structure needs both symbols to agree.** SPY one way and QQQ the other is
  Neutral, and the row shows both.
- **Cross-asset uses the board's polarity, not a second opinion.** Whether a
  falling Treasury fund is good or bad for stocks is already decided, once, by
  `market_svc.classify.color_state`. This row counts those colours.
- **Flow is an estimate and says so** (`estimate: true`; the page prints ≈). It
  is pooled by volume over today's flagged contracts, calls and puts apart, and is
  `none` below `min_contracts`. Measured 2026-10-05: the pooled lean was under two
  points on calls, so this row will read Neutral on most days. That is the honest
  reading, not a fault.

Every threshold is a starting guess. All of them live in `config/market_read.toml`
and Settings → Configuration.

## Since the last update

Each row carries the previous slot's verdict and numbers. The page prints "was
Neutral" when the chip changed, otherwise the change in the row's main number, or
"unchanged". The service keeps the day's slots in the payload (`history`), so a
page reload, a second tab and a service restart all see the same "since".

## Where it runs

`services/market_svc/market_read.py`, pure: six row functions over plain dicts,
`build(...)` to assemble a reading, and the slot arithmetic. The market service
already reads other services' views (`read_sector_pcr`, `read_net_prem`).

- **Slots.** Every `interval_min` (15) on the clock, from the first boundary
  after the 08:30 CT open to the 15:00 close inclusive. The 15:00 reading is
  marked `final` and stays up after hours. Nothing is computed outside the
  session.
- **The loop** asks "is a slot due" on its existing tick; no new timer.
- **A restart** reads the published view back: same date means the history and the
  last slot are restored, so a slot is not published twice and "since" survives.
- **`cache:market:read`**: `{date, ts, slot, next_slot, final, public, tally,
  rows, history}`, validated by a small contract model, published with
  `skip_unchanged`.

## The Desk panel

- Title "Market read", a header line with the slot time, the next slot and the
  tally ("2 tailwinds · 3 headwinds · 1 neutral").
- One line per row: question, reading, since, chip. Module-level builders; the
  Desk's `render` is at its size ceiling, so the painter is a module-level
  function and the wiring is a few lines.
- A reading from another day, or older than two intervals during the session, is
  greyed with its time. After the close the final reading is shown as the close.
- Public origin: shown only when the view's `public` is exactly `true`.

## Configuration (`config/market_read.toml`)

```toml
enabled = true
public = true
interval_min = 15          # 15 or 30
stale_after_sec = 300      # an input view older than this is No reading

[direction]
move_pct = 0.25
[breadth]
strong_share = 0.60
weak_share = 0.40
[structure]
symbols = ["SPY", "QQQ"]
room_pct = 0.50
near_pct = 0.25
[volatility]
vix_move_pct = 1.0
[flow]
lean_pts = 5.0
min_contracts = 10
```

## Failure behaviour

- A failure computing one row makes that row `none`; the others still publish.
- A failure of the whole pass is logged through `_degrade.degraded` and leaves the
  last reading up; the panel greys it once it is two intervals old.
- The pass runs after the dashboard publish, in the same executor call pattern,
  and cannot stop the poll.

## Testing and rollout

- The pure module is test-first: each row's three outcomes and its missing-input
  case, the slot arithmetic across the open and the close, the restart restore,
  and the tally.
- A producer-side test drives `none` end to end (a view with no tiles through to
  a published `none`), because a consumer-side guard proves nothing on its own.
- The panel is verified on the local page harness; the service by a Redis read on
  prod after promote.
- The hover guide, the manuals, the routes file, the config reference and the
  changelog move in the same commits.

## What review changed

An independent review of the first build, before it was merged, changed these rules.
The text above is the design as approved; where the two differ, this section is what
the code does.

- **Structure is gated by the collector's age, not the matrix's.** The matrix is
  republished every minute whether or not the collector ran. The row reads
  `options:gex_status` (`age_seconds` plus the status view's own age) and is "No
  reading" past `[structure] stale_after_sec` (150, the dealer panel's limit) or
  when the age is unknown. A fifth input view.
- **Volatility needs all five numbers.** With the one-day VIX missing the first
  build could say Tailwind without the check that most often overrides it.
- **The switches apply between slots.** `enabled = false` publishes a retraction
  (`enabled: false`, no rows) once and the Desk hides the card; a changed `public`
  republishes the reading under the new flag.
- **The Flow row carries the estimate's own public flag.** On the public origin a
  row not marked public is drawn as "Not shown on this screen" and left out of the
  tally. The row reads `options:flow_sides` alone, which now names each entry's
  `side` and `osi`; the join to the capped alert list is gone.
- **Breadth needs `min_tiles` (10) priced tiles**, and an all-flat board is Neutral.
- **Cross-asset** with a tile missing is "No reading" unless two already agree.
- **Sources are all judged by age**; the dashboard has its own limit (60 s); the
  flow view is judged by date as well.
- **A slot at or before the published one is never due; a failed build backs off
  `retry_sec` (30); `near_pct` must sit below `room_pct`.**

## Not in this build

- The verdict sentence and the three-pillar layout.
- The intraday timeline (the history is kept so it can be added without a
  service change).
- Any alert, push or spoken line when a chip changes.
