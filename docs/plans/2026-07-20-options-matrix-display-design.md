# Options Matrix Display — design (2026-07-20)

A new **Matrix Display** tab under the Options menu: one row per watchlist stock,
showing each name's live state at a glance so opportunities float to the top.

## Purpose

**Spot opportunities.** A live, sortable grid of every watchlist symbol so the user
can scan the whole universe and immediately see which names are "hot" (most signals,
most flow alerts, strongest trend, dominant call/put flow) and where to dig in. Not a
position monitor, not a pre-baked shortlist — a fast at-a-glance scanner.

## Placement

- **Main menu item under Options.** New tab in the Options nav strip
  (`webgui/main.py` `_NAV_GROUPS` Options children), route **`/options/matrix`**,
  alongside Scanner / Gamma / Rescue / etc.

## Architecture — new aggregator in `options_svc`, matrix page is a pure reader

`options_svc` is the only process that already holds every ingredient in one place:
the `gex_history.db` connection (per-symbol spot + call/put premium series), the
`cache:options:scan_day` signals, and `cache:options:flow_alerts`. So the aggregation
lives there and the webgui stays a thin Tier-1 reader (3-tier rule preserved).

- **`compute.build_matrix()`** — assembles one row per watchlist symbol
  (`gex_collector.collection_symbols()` minus `$VIX`, ~24 names) into a new
  **`cache:options:matrix`** view, validated by a new **`MatrixSnapshot`** contract
  (`shared/contracts/options.py`).
- **Publish cadence:**
  - Rebuilt on each **1-min GEX poll tick** (trend / flow / GEX / counts are all 1-min
    data anyway) via a new `handlers` hook.
  - A lighter **~30 s spot refresh** — one batched `get_quotes` over the watchlist so
    **Spot** and **Day %** feel live without re-fetching chains.
  - `cache_set(..., skip_unchanged=True)` so an unchanged grid neither re-publishes nor
    forces a webgui repaint.
- **Webgui `/matrix` tab** (`webgui/pages/options/matrix.py`) — version-polls
  `cache:options:matrix` every ~2 s and updates rows **in place** (Market-Dashboard
  pattern). No engine imports.

**Live-ness, honestly:** Spot + Day % refresh ~30 s (batched quote); trend / flow /
GEX / signal-counts refresh ~1 min (the GEX-poll cadence). The webgui polls ~2 s and
repaints only on a version change.

**Rejected alternatives:** (a) webgui opens `gex_history.db` directly — breaks the
3-tier rule and duplicates GEX logic; (b) a whole new `matrix_svc` — needless, since
`options_svc` already owns every input.

## Columns

| # | Column | Source | Notes |
|---|--------|--------|-------|
| 1 | Ticker | `collection_symbols()` minus `$VIX` | sortable |
| 2 | Spot | batched `get_quotes` (~30 s) | falls back to latest `gex_history` spot |
| 3 | Day % | quote vs session open | green/red |
| 4 | Intraday trend | spot slope / EMA over today's flow series | ▲▲/▲/▬/▼/▼▼ + strength |
| 5 | Call trend | call-premium **acceleration** (recent slope vs day-avg slope) | ▲/▼ + intensity |
| 6 | Put trend | put-premium acceleration | ▲/▼ + intensity |
| 7 | Put/Call ratio | put ÷ call premium today | sentiment gauge |
| 8 | Net premium ($M) | call − put premium | money-weighted lean |
| 9 | GEX regime | spot vs gamma flip (from latest stored per-strike grid) | above=supportive / below=unstable |
| 10 | # Signals | `cache:options:scan_day` grouped by `symbol` (gate on `date`) | today's count |
| 11 | # Flow alerts | `cache:options:flow_alerts` grouped by `symbol` | today's count (rolling ~50-cap caveat) |
| 12 | **Buy/Sell** | options-flow composite | headline lean badge |

## Derived-column logic (pure functions over the day's 1-min flow series)

- **Intraday trend (#4):** compare current spot to a short EMA of today's spot series
  and the % move over the trailing ~15 min; normalize → bucketed score →
  ▲▲/▲/▬/▼/▼▼. Strength = magnitude of the normalized move.
- **Call / Put trend (#5/#6) — acceleration:** cumulative premium only rises, so the
  signal is *rate*. Recent slope (premium added in the last ~15 min) vs the day's
  average slope → ratio > 1 = accelerating ▲, < 1 = cooling ▼; intensity from the
  ratio. Answers "is call-buying / put-buying heating up right now."
- **Buy/Sell composite (#12):** a small weighted blend of **(a)** intraday price trend
  and **(b)** flow lean (sign of net premium & the P/C ratio, weighted by call/put
  acceleration). Calls dominant + price up → **Buy**; puts dominant + price down →
  **Sell**; weak/conflicting → **Neutral**. A 3-state badge with a strength tier. All
  thresholds are named, tunable constants; degrades to **Neutral** on too-little data.

## UI (`webgui/pages/options/matrix.py`, Tailwind-first)

- A sortable `ui.table`, one row per symbol, ~12 columns. Colored cells: Day% green/red,
  trend arrows colored by direction, P/C ratio & net-premium tinted, a GEX-regime chip
  (above-flip green / below-flip red), a **Buy/Neutral/Sell badge** (green/amber/red).
- **Default sort = "hotness"** (signals + flow alerts + |trend|) so opportunities float
  to the top; every column click-sortable.
- Rows update **in place** on the ~2 s version-poll (no rebuild).
- Off-hours shows the last session with a note; symbols missing a flow series show `—`,
  never an error.
- Row click → optional deep-link to open that symbol in Gamma (nice-to-have).

## Testing (TDD per the app norm)

- Pure builders unit-tested with synthetic inputs: `compute.build_matrix` (fake
  `gex_history` rows + `scan_day` + `flow_alerts`), the trend / acceleration / composite
  helpers, and the webgui row/color builders.
- `MatrixSnapshot` contract validation.
- Live-verify Redis-driven (`Bus().cache_get("cache:options:matrix")`) + a browser
  screenshot of the rendered grid.

## Gaps this closes (vs. today)

- No Redis per-symbol spot / call-put trend / intraday trend / Buy-Sell for the
  watchlist existed — the raw ingredients live only in `gex_history.db`. This design
  publishes them as one `cache:options:matrix` view.
- `cache:options:scan_day` (# signals) and `cache:options:flow_alerts` (# flow alerts)
  are already clean Redis reads, folded into the same view.
