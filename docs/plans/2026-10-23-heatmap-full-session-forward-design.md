# Dealer Positioning heatmaps: the whole session, with projected contours

Date: 2026-10-23. Builds on
[the forward projection (2026-07-11)](2026-07-11-gamma-forward-projection-design.md),
[value / scale / frame (2026-10-09)](2026-10-09-gamma-heatmap-value-scale-frame-design.md)
and [smoothing (2026-10-11)](2026-10-11-gamma-heatmap-smoothing-design.md).

## Intent

From the first minute of the regular session, every Dealer Positioning heatmap
(GEX, Charm, DEX, Vanna) spans the whole session, 08:30 to 15:00 CT. Left of a
dashed "now" line are the collected minutes, as today. Right of it are projected
cells running to the close. The contour lines are traced across both as one
picture, so a line that forms in the morning carries on into the afternoon and
shows where each Greek is heading if price stands still.

The operator's words: the contour lines "will have the same function as what is
displayed now". They mean the same thing, at the same levels, behind the same
switch. The only change is that they now reach into the future.

## Today (what this replaces)

- Only GEX has a forward band (`compute.project_gex_grid`, attached as
  `entry["projection"]`). It uses 15-minute marks, and each mark takes ONE
  column, so a 15-minute column sits beside 1-minute columns and the time axis is
  not uniform.
- The band is net only. `heat_keeps_projection` drops it for Calls, Puts and
  Size, for the Share scale, for the "From spot" frame and for both Change views.
- Contours are traced from the collected columns only (`heatmap_figure` passes
  `vz` before the band is appended).
- The band is deliberately not smoothed (`test_the_forward_band_is_not_smoothed`).
- `_future_marks_ct` returns marks whenever the clock is before 15:00, on any
  day. Weekends and the pre-market are not excluded by it.

## Goals

1. All four Greek heatmaps show 08:30–15:00 once the session has opened, with
   one column per minute across the whole width.
2. Contours run across collected and projected cells without a break at "now".
   They use the same levels (`[contours]` steps), colours, minimum span and point
   budget as today.
3. Every value (Net, Calls, Puts, Size), scale (Adaptive, Locked, Share), frame
   (Strike, From spot), show (Level, Change since open, Change over N min) and
   smoothing level draws the projected cells too. The projection goes through the
   SAME pipeline as the collected cells, so nothing is special-cased.
4. The hedge panel under the heatmap keeps its column alignment. It already
   builds on the heatmap's full category list, so it widens with the chart.

## Non-goals

- No change to the projection MODEL beyond extending it to all four Greeks.
  Spot, open interest and implied volatility are held at their current values.
  Only time to expiry moves.
- No change to the by-strike bars, the "Proj. close" outlines, the projected flip,
  the Claude briefing's `_projection_brief`, or the Term view.
- No pre-market band. Before 08:30 the heatmap behaves as today.
- No projection of the flip / wall level tracks into the future.

## Anti-goals (a failure even if it technically works)

- A projected cell or line that can be mistaken for collected data. The dashed
  "now" line stays, and projected contour segments are dashed.
- A visible jump in colour or in a contour at "now" caused by the method rather
  than by the data.
- Making the per-minute publish noticeably slower or larger. The hot-set
  public symbols are re-published every minute, each running `gamma_snapshot`.
- A band on a weekend, a holiday, before the open or after the close.

## Decisions

Decided by the operator:

- The whole session is shown from the open, on all four Greek heatmaps.
- Contours work as they do now and extend into the projected part.
- Every value, scale, frame, show and smoothing level gets the projected cells
  (played back and accepted).
- Smoothing covers the whole picture (played back and accepted). This reverses
  the 2026-10-11 rule that the band is never smoothed. Reason: a smoothed
  collected column beside an unsmoothed projected one draws a seam in both the
  colours and the lines.
- **The Adaptive colour scale, and so the contour levels, are set from the whole
  session**, collected and projected cells together, as the GEX band's scale is
  today. Asked directly (2026-10-23), with the consequence stated: at the open
  the large at-the-money cells of today's expiry in the afternoon set the scale,
  and the collected morning reads fainter. The Locked scale is unchanged (set
  from the collected first hour).

Claude's calls (the operator can overrule any of them):

1. **Projected contour segments are dashed.** The cells are not marked
   differently. The dashed "now" divider already separates them, and the cells
   blend into one picture.
2. **The service computes every `[forward] step_min` minutes (5), and the page
   fills the minutes in between** by linear interpolation in time. This keeps the
   payload to about a fifth of a per-minute grid.
3. **The forward data travels with each view's HISTORY payload, not the main
   key.** The page reads only the history of the view on screen, so a view not
   being drawn costs nothing. It also follows the existing rule that the main key
   stays slim (the 2026-08-20 split).
4. **`entry["projection"]` is retired.** It is replaced by the per-view forward
   data, and `project_gex_grid` stays only for `_projection_brief`.
5. **The cone (EM up / down) and the flat spot line continue to the close as
   today**, filled to one point per minute like the cells.
6. **The tooltip is unchanged.** A projected cell says what a collected one says.
   The dashed divider is what tells them apart.
7. **A kill switch:** `[forward] enabled = true` in `config/gamma_heat.toml`.
   Off, the heatmaps show the collected minutes only (no band on any view,
   including GEX).
8. **Time to expiry keeps the engine's fixed 15:00 rule** (`gamma_tool`
   `CLOSE_HOUR_CT`), so the value at the `now` mark equals the collected cell on
   every day. The marks stop a minute before `regular_close_on`, which reads
   `sessions.regular` and is 15:00 on every date: the calendar knows holidays but
   no early closes, so a half day is drawn as a full one, exactly as the collected
   cells already price it. Teaching the calendar and the engine an early close
   would touch the collector, the raw-feed replay and stored history; that is a
   separate piece of work.
9. **The last projected column is one minute before the close** (14:59 on a
   full day). At the close itself T hits its 1e-6 floor and at-the-money gamma,
   charm and vanna are effectively infinite, which says nothing a reader can
   use. The close's own column appears when it is collected.
10. **The forward data and the history rows are published together**, in the
    same payload from the same snapshot, so the `now` mark always belongs to the
    last collected row's snapshot. The page still starts its forward rows at the
    minute after the last collected row, and a minute before the first mark
    takes the first mark's value.

## How it works

### Service (`services/options_svc`)

A new sibling module (CLAUDE.md: new service code does not grow `compute.py`),
for example `forward_grid.py`, importing nothing from `compute`:

- **When.** A forward grid is built only when ALL of these hold: today is a
  trading day (`shared.market_calendar`), the regular session has opened, `now`
  is before the regular close for today (`regular_close_on`, so a half-day
  closes early), and `[forward] enabled`. Otherwise no forward data is attached.
  This fixes the existing weekend gap for GEX as well.
- **Marks.** `now`, then every `step_min` minutes on the clock (:00, :05, …)
  after it, then one minute before the day's close (decision 9). Each mark is
  sent as epoch seconds and as `HH:MM`.
- **Time to expiry** at a mark uses the engine's own rule: the nearest
  expiration's DTE plus the hours left to 15:00 CT, over 365 × 24, floored at
  1e-6 (`_T_at`; decision 8).
- **The contracts** are the nearest expiration only, exactly the set
  `calc_all_from_chain` uses. Only strikes inside the crop window
  (`gamma_window.crop_keep`, the same keep set `_crop_gamma_views` applies) are
  priced. That bounds the work to the strikes that can be drawn.
- **Per view, per side (call and put), per strike, per mark**, using the SAME
  formula and units as `calc_all_from_chain`, so that the value at the `now` mark
  equals the collected cell:
  - **GEX:** the chain's gamma × the ratio `bs_gamma(T') / bs_gamma(T_now)`,
    as `project_gex_grid` does today. A contract with no usable volatility holds
    flat.
  - **Charm, Vanna:** Black-Scholes at `T'` directly. The collected cell is
    Black-Scholes at `T_now` with the same inputs, so the seam is exact.
  - **DEX:** the chain's delta plus `bs_delta(T') − bs_delta(T_now)`, an additive
    offset (a ratio of deltas misbehaves near zero). Where the collected cell used
    the Black-Scholes fallback (no usable chain delta), use `bs_delta(T')`.
- **Speed.** Vectorise with numpy over contracts × marks. The existing `_v`
  functions take one volatility and one time, so `options_calculator` gains
  `bs_greek_arrays(S, K, T, r, sigma)` (gamma, charm, vanna and call delta,
  broadcast element by element), tested against the scalar functions point by
  point. Measured on a 61-strike `$SPX`-sized chain at 08:31 (79 marks, all four
  views): 60 ms, 90% of it rounding one float at a time; rounded in numpy, 6 ms.
- **Shape.** Per view:
  `{"ts": [...], "times": [...], "spot": s, "cone": {"mid", "up", "down"},
  "call": {strike: [...]}, "put": {strike: [...]}}`. Values are rounded to the
  history's 6 significant figures. Net is not sent; the page adds call and put.
  The cone comes from `project_em_cone` over the same marks.
- **Publish.** `_publish_gamma` pops `forward` out of each view entry beside
  `history` and writes it into that view's history payload
  (`{"symbol", "view", "rows", "forward"}`), for the private and published
  targets alike. A view with no forward data writes `forward: None`.

### Page (`webgui/pages/options`)

- **Forward rows.** A pure helper in `gamma_heat.py` turns a view's forward
  data into history-shaped rows: one row per minute from the minute after the
  last collected row to the close. Each row has the epoch ts, the flat spot,
  `None` for flip / walls / totals, and a grid of `{call, put, net}` per strike,
  linearly interpolated between the two marks around it. Net is call + put.
- **One pipeline.** `heatmap_figure` gets the collected rows plus the forward
  rows as ONE list, so `heatmap_matrix`, `delta`, `to_spot_frame`, `smooth`,
  `share_of_column`, `scale_max` and `contours` all see the whole session. It
  also gets the count of collected rows, which it uses for:
  - the "now" divider, at that column − 0.5;
  - the spot line, candles and OHLC, drawn from collected spots only, with the
    spot line then continued flat along the cone mid (as today);
  - the level tracks, which have no values past "now";
  - dashing the contours: the two contour series take
    `zoneAxis: "x"` with a zone that turns `dashStyle` to a dash at the divider.
    The series count does not change. If zones turn out not to draw on a
    scatter with a line width, split each sign into a collected series and a
    projected series instead, and keep the count fixed in every view and mode.
- **Collected rows only** go to everything that means "now" or "what happened":
  the bars' change basis (`basis_grid`), the hedge panel's `col_ts`, the
  balanced marks, and the scale lock.
- **`heat_keeps_projection` is removed.** Every control combination draws the
  band. `heatmap_categories` takes the forward rows, so the hedge panel's
  categories match the heatmap's.
- **Smoothing** runs over the whole grid. `test_the_forward_band_is_not_smoothed`
  is replaced by a test that the seam column is smoothed with its neighbours on
  both sides.

### Configuration

`config/gamma_heat.toml` gains:

```toml
[forward]
# The heatmaps run to the close from the open: right of the "now" line are
# cells projected with price, open interest and volatility held where they are.
enabled = true
# The service prices the projection every this many minutes; the page fills
# the minutes between.
step_min = 5
```

Both are read through `shared/gamma_heat_config.py`, per paint / per publish
(no restart), and each gets an entry in `webgui/config_schema.py`.

## Constraints

- Tier 1 stays pure: the forward-row helper is plain arithmetic in
  `gamma_heat.py`. No engine import, no numpy in the webgui.
- The heatmap's series count stays fixed across views and modes (the in-place
  `chart.update` rule).
- The contour series keeps a new `pointStart` on every build
  (`gamma._CONTOUR_BUILD`). Check it with a repaint, not a toggle.
- The time axis stays pinned at 0 … n−1 (`_pin_time_axis`) and the strike axis
  on the ladder (`raster_range`). With ~390 columns from the open, check that the
  picture still lands on the axis.
- `gamma.render`'s size ceiling can only be lowered. New logic goes in helpers.
- The manuals: `webgui/page_help.py` (`PROJECTION_HELP_MD` and the heatmap help),
  the User Guide and the Technical Reference describe the GEX-only, 15-minute
  band. They change in the same commit.

## Cost to measure before it ships

- `gamma_snapshot`'s added time per symbol with all four forward grids, on a
  real `$SPX` chain at 08:31 (the most marks). Budget: under 50 ms.
- The history payload's added size per view at 08:31 (the most marks). Expected
  at a 41-strike window, 80 marks and two sides: about 6,600 numbers, around
  70 KB of JSON per view. Measured: 75–97 KB at 41 strikes and 111–134 KB at 61,
  falling through the day to a few KB by 14:50.
- The browser: a heatmap of ~390 columns plus contours at 08:31 is the same cell
  count as today's chart at 15:00, which already renders. Confirm the repaint
  stays smooth with Contours and Smooth on. Measured in Python
  (`heatmap_figure`, 61 strikes, Contours and Smooth Medium on): 249 ms at 08:31
  with the band against 193 ms for a collected 15:00 chart.

## Left to the builder

- Module and function names, and how the forward-row helper is split.
- Whether interpolation between marks happens on call and put separately (it
  must, so Size and the sides stay consistent) — and how a strike present at one
  mark and absent at the next is treated (a gap, never zero).
- Test fixtures and the harness scenario that shows the band at 08:31, 12:00
  and 14:58.
