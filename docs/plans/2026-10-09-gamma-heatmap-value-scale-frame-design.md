# Dealer Positioning heatmap: value, scale and frame (design)

**Date:** 2026-10-09
**Scope:** `webgui/pages/options/gamma.py` and a new sibling `gamma_heat.py`
(Tier 1); two small additive changes in `services/options_svc` (Tier 2); one new
config file. No new command, no new write path, no schema change.
**Mock-up:** https://claude.ai/artifact/BVUesjqSCzeK2vo2DpK6hb (synthetic data).
Three things in it are changed by this design; they are listed in section 9.

## What changes

The intraday heatmap on `/options/gamma` draws one number per cell (`net`), on a
colour scale that is recomputed every repaint and has no legend, against a strike
axis. This design adds four controls that change those three things, and leaves
the chart's structure alone:

| Control | Options | What it changes |
|---|---|---|
| **Value** | Net · Calls · Puts · Size · Premium | which number a cell holds |
| **Show** | Level · Change since open · Change over 30 min | the number, or how it moved |
| **Scale** | Locked · Adaptive · Share of column | what a colour means, plus a legend |
| **Frame** | Strike · From spot | what the vertical axis measures |

They apply to the four Greek views (GEX, Charm, DEX, Vanna). Flow, Net Prem and
Term are untouched.

## Why

Three facts about the current chart, each read from the code:

1. **A balanced strike is invisible.** Every stored cell is `{call, put, net}`
   (`gamma_tool.py`: calls add, puts subtract), but `heatmap_matrix` reads only
   `net`, and zero maps to the transparent stop. A strike with a large call
   position and an equal put position draws exactly like a strike with nothing
   on it.
2. **A colour has no fixed meaning.** `heatmap_figure` sets the scale to the
   95th percentile of the visible cells on every repaint, and `_coloraxis`
   disables the labels. The same cyan is a different dollar amount at 09:00 and
   at 14:00, and on SPY and on $SPX, and nothing on the page says which.
3. **The levels do not move, because the inputs do not.** The collector weights
   by open interest (`gex_collector.py`: `calc_all_from_chain(chain,
   use_volume=False)`), which updates once a day. Through a session the field
   changes only as spot, time and volatility reprice the same positions, so the
   chart is mostly horizontal bands. The per-strike traded premium the collector
   has stored since 2026-08-15 (the `prem` view) is the only per-strike record
   of today's activity, and it is drawn only as the Flow view's 11-row ladder.

## Facts that shape the design

| Fact | Source | Consequence |
|---|---|---|
| The interpolated heatmap colours each pixel as `series.colorAxis.toColor(value, point)`. A per-point `color` is not read. | Highcharts 12.4.0 as bundled (`nicegui_highcharts/dist/heatmap-*.js`, `colorFromPoint`) | A cell can encode one number on one axis. A true two-value cell (hue from one, brightness from another) is not available without a second layered series. See section 9. |
| The heatmap's series count is fixed at 9 and its colour axis must exist when the element is created. | `heatmap_figure`, `_heat_init_fig` | Every mode here swaps the data inside the existing series. No series is added and the stops list is never changed at runtime. |
| `gamma.render` sits at its size ceiling (1,484 lines, 62 nested functions) and the ceiling may only be lowered. | `webgui/tests/test_render_size.py` | The four controls and their handlers are built by a module-level function. Render must come out smaller than it went in. |
| History rows arrive through `bus_client.read_shared`, so every tab at one version holds the same object. | `gamma._load_history` | Every transform builds new lists. None may write to a row or a grid. |
| The service crops each history grid to 20 strikes each side of the current spot, widened to the span of the session's spot path. | `compute._crop_gamma_views` | Enough for the strike frame. Not enough for the spot frame on a trending day. See section 5. |
| The heatmap and the hedge panel under it align only because both run zero left and right margins on the same category list. | `hedge_figure`, `heatmap_categories` | The legend is its own element in the controls row. It cannot be a Highcharts legend inside the plot, or a column beside it. |
| Stored premium is day-cumulative `Σ mark × totalVolume × 100` per strike, unsigned, mid-based. | `flow_skew.premium_by_strike` | Level is "since the open". A difference of two readings mixes new volume with the re-marking of earlier volume. See section 7. |
| The one-minute GEX branch already overruns its minute a few times a day, and a hot public symbol costs about 4 MB of Redis writes a minute. | `config/gamma_public.toml` | Anything added to that branch is measured before it ships, and Premium is the last phase. |

## 1. Value

One pure function turns a history row's grid into the number each cell draws:

| Value | Cell | Sign |
|---|---|---|
| Net | `net` | as stored (today's chart) |
| Calls | `call` | as stored |
| Puts | `put` | as stored |
| Size | `abs(call) + abs(put)`, signed by `net` | call side when `net >= 0` |
| Premium | the `prem` view's `net` (call dollars minus put dollars) | section 7 |

**Size is the fix for the balanced strike.** It answers "how much is here" with
magnitude and "which way does it lean" with hue, on the existing diverging axis.
The balanced strike becomes one of the brightest rows on the chart.

Its weakness is that a strike with almost no lean takes its hue from a small net
that can change sign. Two things cover that:

- **Balanced-strike markers.** A strike whose polarity `abs(net) / size` is under
  a threshold and whose size is in the top band of the visible window gets a thin
  grey dotted line on the strike axis, labelled "Balanced", in both panels (grey
  because amber is the projected flip's and lavender the flip's). At most
  three, largest first. They are y-axis plot lines in the list `wall_plot_lines`
  already emits on every paint, so they need no series.
- **Split bars.** In Size mode the by-strike panel draws calls and puts as two
  opposing bars per strike, in the two per-sign series it already has. A balanced
  strike is then two equal bars, which no hue is needed to read.

In Calls and Puts the bar panel draws that side alone. In Net it is unchanged.

A legacy row whose cells are bare numbers has no call or put. If no cell in the
loaded session has them, Calls, Puts and Size are disabled with a one-line
reason. They are never drawn as zero.

## 2. Show

`Level` draws the value. The two `Change` options draw `value[t] − value[t0]`
per strike, where `t0` is the first regular-session column or the column 30
minutes earlier. A strike with no reading at `t0` is a gap, never a zero.

This is a separate control and not a sixth Value, because it composes with every
value for free: one subtraction applied after the value is taken. Change of
Calls, of Size and of Premium all come from the same function.

**What Change means has to be said on the page.** Open interest is fixed for the
day, so for the four Greek values a change is the same positions repricing as
price, time and volatility move. It is not new trades. The control's tooltip and
the page guide say so. (They will point at Premium for new activity once Premium
exists; until then they do not name a view the page does not have.) The clearest
thing it shows is the 0-DTE build at the money into the close, which the level
view buries under the standing bands.

**The bars follow.** The by-strike panel draws the same change the heatmap's last
column shows: each strike's value now, less its value in the row that column is
measured from (`gamma_heat.basis_grid`). Two panels showing different quantities
under one picker would mislead. A strike the basis does not hold gets no bar, and
in the first minutes of "Change over 30 min", when nothing is old enough, there
are no bars and the heatmap is empty: never the level under a title that says
change. The forward projection band and the bars' projected-close outline are
levels, so neither is drawn in a change view.

## 3. Scale and the legend

| Scale | Colour means | Use |
|---|---|---|
| Locked (default) | a fixed dollar amount for the whole session | reading size |
| Adaptive | a fraction of the 95th percentile of what is visible now | today's behaviour; seeing detail on a quiet view |
| Share of column | this strike's share of that minute's total size | comparing shape across times and symbols |

**The lock is computed once, by the service, and published.** For each symbol and
view, after the first `lock_minutes` of the regular session, the service takes
the 95th percentile of the absolute cell over those rows, within the display
window around each row's own spot, for each of Net, Calls, Puts and Size, times a
headroom factor. It publishes the four numbers in the view's entry of the
snapshot:

```
views.GEX.scale_lock = {"minutes": 60, "net": …, "call": …, "put": …, "size": …}
```

It is computed from the uncropped rows the history memo already holds, memoized
for the session, and costs nothing after the first build.

The page could compute a lock itself, and the mock-up does. It should not: the
page holds grids cropped to a window that moves with spot, so a page-side lock
would drift through the day while the legend claimed it was held. A locked scale
that moves is worse than an adaptive one that says so.

Rules that follow from it:

- Before the lock exists (the session's first hour) the chart is adaptive and the
  legend says "settling until 09:30". With no lock once that time has passed (a
  payload that predates the field, or rows the service could not read) the legend
  says the scale adapts: there is nothing left to wait for.
- A Change of a Greek value uses that value's Level lock. A change cell as bright
  as a wall is then as large as a wall, and one legend serves both.
- Premium does not offer Locked. Its level grows all day by construction.
- The by-strike bars take the lock as a **soft** extent in Locked: the axis holds
  at the lock, so a bar's length means the same amount all session, and widens for
  a bar larger than the lock. A hard maximum would cut the largest bars off flat,
  which loses exactly the strikes the reader most needs to size. In Size the bars
  are a call bar and a put bar, so their extent is the larger of those two locks.
- The forward projection band is raw net exposure, so it is dropped on a share
  scale as well as for a non-net value.

**The legend** is a horizontal strip in the controls row: the two ends of the
scale in numbers through `pages/fmt.py`, the ramp between them, the unit, and one
caption (`held since 09:30`, `adapts to what is visible`, `share of each
column`). It is not a column beside the chart, because the heatmap runs flush to
the window's right edge by design and must stay the width of the hedge panel
under it. GEX cells are dollars of gamma per 1% move in the underlying and DEX
cells are dollars of delta (`gamma_tool.py`), so those two print a dollar unit;
Charm and Vanna print the bare number, as the bars' axis does today. It is an SVG
fragment through `ui.html`, with its tags tested against the shipped allow-list,
and it is hidden outside the four Greek views.

## 4. Frame: strike

Unchanged, and the default.

## 5. Frame: from spot

The vertical axis becomes the distance from spot, in the underlying's points.
Price is a flat line through the middle. The flip and the walls become moving
tracks: a wall that price is approaching slides toward the centre, and the slope
is the closing speed. It answers "what is near me, and is it getting nearer"
without comparing a moving line against fixed rows.

**Page.** Each column is resampled onto a uniform offset ladder (the strike step,
`spot_side` strikes each side) by linear interpolation between that column's strikes,
centred on that column's own spot. An offset the column has no strikes around is
a gap. Nothing is extrapolated. Then:

- the Spot series is a line at zero, and the Spot style and Bar pickers hide,
  since a candle of price against itself is nothing;
- the three level tracks are drawn as `level − spot` per column and are always
  on in this frame, whatever the Level movement switch says, because they are
  the read;
- the static flip, wall and projected-flip lines sit at their current offsets;
- the bars are drawn at `strike − spot now`, so the two panels still share one
  axis and the crosshair still marks the same row in both;
- the GEX forward projection band is not drawn in this frame in the first
  version. The hedge panel under the heatmap must then be built on the same
  shortened category list, which is the pairing `heatmap_categories` exists for.

**Service.** The crop keeps 20 strikes each side of the current spot, plus the
strikes between the session's low and high. A column at the session low
therefore has almost nothing below it, and on a trending day (the day this view
is most useful) half of the early columns would be gaps. On a 1.8% trending
seed, 88 of 180 columns came up short. So the crop also keeps `spot_side` strikes
each side of the session's low and high, plus one: price is rarely exactly on a
strike, so the frame's outermost row falls between the `spot_side`-th strike and
the next, and interpolating it needs both.

**What it costs, measured.** This section first said the frame would be the full
20 strikes tall and that the wider crop would cost "under a third more on the
widest days and nothing on a quiet one". That was measured before building, on
six stored prod sessions, and it was wrong: 20 strikes added 40% to `$SPX`'s
history on a day with a 1.1% range and 13% on the quietest day. By the rule's
arithmetic (no stored day was that wide, so this is not measured) a 2% day that
closed mid-range would add roughly 70%. Each view's history is rewritten every
minute, on a branch that already overruns.

So the frame is **10 strikes tall**, and that height is its own config key
(`[window] spot_side`), which both the page and the service read. At 10 the
measured cost is 12% on `$SPX`'s widest stored day, 9% on QQQ and `$NDX`, and
nothing on SPY, NVDA or a quiet day. `spot_side = 0` restores the crop exactly as
it was; the frame is then the display window tall and shows gaps.
`tools/measure_gamma_crop.py` repeats the measurement on any stored session.

`N_SIDE` (page) and `GAMMA_N_SIDE` (service) were the same literal in two tiers.
Both are gone: `[window] n_side` is read at call time by each tier, so they agree
within a minute of a change and neither needs a restart.

## 6. Where the code goes

```
webgui/pages/options/gamma_heat.py      new, pure, imports nothing from gamma
    cell_value(cell, mode)              Value
    delta(z, basis)                     Show
    scale_max(z, scale, lock, mode)     Scale
    share_of_column(z)
    to_spot_frame(strikes, z, spots, step, half)   Frame
    balanced_marks(strikes, calls, puts, …)
    legend_svg(zmax, unit, caption)
    build_controls(on_change, public)   the four controls, one callback
```

`heatmap_matrix` and `bar_figure` gain a `mode` argument and call these.
`heatmap_figure` gains `show`, `scale`, `lock` and `frame`. Its series list, its
colour axis and its load hook do not change, and the tests that pin the count of
nine and the palette stay as they are.

`gamma.render` gains one call to `build_controls` and one handler. To pay for
them, the three existing overlay controls (Level movement, Spot, Bar) and their
three handlers move into the same builder. Render ends smaller and its ceiling
is lowered in the same commit.

The four choices persist in `app_settings` (`gamma_heat_value`, `_show`,
`_scale`, `_frame`) on the private page. On the public page settings are frozen,
so they are per-visitor element state, the way Level movement is today, and the
four keys join the frozen list.

**Config** (`config/gamma_heat.toml`, each key in `webgui/config_schema.py`):

| Key | Default | Read by |
|---|---|---|
| `[lock] minutes` | 60 | service |
| `[lock] quantile` | 0.95 | service |
| `[lock] headroom` | 1.5 | service |
| `[show] change_window_min` | 30 | page |
| `[balanced] max_polarity` | 0.15 | page |
| `[balanced] min_size_quantile` | 0.80 | page |
| `[balanced] max_marks` | 3 | page |
| `[window] n_side` | 20 | both (replaces the two literals) |
| `[window] spot_side` | 10 | both (the spot frame's height, and the crop for it) |

## 7. Premium

**Not built. It stopped at its gate on 2026-10-09; the measurement and what it
means are at the end of this section. The four paragraphs below are the design as
first written, kept because the result is a verdict on them.**

The only part that needs a new key, and the last phase.

**Service.** The `prem` rows are already loaded every minute for the ladder,
through the same memo as the Greek views. They are cropped to the same window
and published as a fifth history key, `cache:options:gamma_hist_prem`, written
before the main key like the other four. The page reads it only when Value is
Premium, through the lazy path that already loads one view's history at a time.
The first version is the private page only. On the public page every leased
symbol would write a fifth history key each minute, and that cost has not been
measured, so the public picker leaves Premium out until it has.

**What it shows.** Level is net premium by strike since the open. Change over 30
minutes is the premium that moved in that window, which is the reading the rest
of the chart cannot give: where money went, laid on where dealers are positioned.
It is unsigned and mid-based, exactly as the Flow view's ladder is, and the page
guide repeats that it is not a buy/sell split.

**The gate.** A day-cumulative `mark × volume` is not monotonic: when a contract's
mark falls, the value of the volume already traded falls with it. A difference of
two readings therefore contains re-marking as well as new trades, and how much
has not been measured. Before Premium ships, the plan measures one stored session
per symbol class (index, ETF, single name): the share of minute-cells where the
cumulative fell, and their dollar sum against the sum of the rises. If
re-marking is a small fraction, Change of Premium ships as designed. If it is
not, Premium ships with Level only, and the increment (`Δvolume × mark`) becomes
a collector change of its own.

**Cost.** One more key rewritten each minute on a branch that already overruns.
The plan measures the publish before and after on a close-of-session shape, as
the 2026-09-21 public histories were.

### The gate, measured (2026-10-09)

Measured on stored prod sessions (the 2026-09-25 backup) with
`tools/measure_prem_remark.py`. A true traded total can never fall, so on each side
of each strike every fall in the stored figure is the mark moving.

| Symbol, session | Minute to minute: falls as a share of rises | Over 30 min: falls as a share of rises | 30-min comparisons that fell |
|---|---|---|---|
| `$SPX`, 09-21 | 73.2% | 8.4% | 16.4% |
| `$SPX`, 09-24 | 88.6% | 54.9% | 21.7% |
| SPY, 09-21 | 64.1% | 6.0% | 16.9% |
| QQQ, 09-21 | 60.7% | 4.3% | 14.6% |
| NVDA, 09-21 | 69.8% | 12.3% | 15.8% |
| NVDA, 09-24 | 66.4% | 16.9% | 19.0% |
| TSLA, 09-21 | 79.9% | 36.5% | 13.2% |

The gate was a tenth. Nothing passes it minute to minute, and four of seven fail it
over 30 minutes. One 30-minute comparison in six is negative, which a traded total
cannot be. **A Change view of this premium would be largely artifact.**

The fallback above ("ships with Level only") does not survive either. The stored
figure is not a cumulative of what traded: it is the day's volume valued at the
CURRENT mark, so it shrinks as marks decay.

| Symbol, session | Day's total: peak | Day's total: close | Minutes in which the total fell | Peak dollars on strikes that closed under half their peak |
|---|---|---|---|---|
| `$SPX`, 09-21 | $8.13B | $5.78B | 36% | 17% |
| `$SPX`, 09-24 | $2.82B | $1.98B | 38% | 46% |
| SPY, 09-21 | $2.63B | $2.15B | 33% | 12% |
| NVDA, 09-21 | $0.46B | $0.36B | 41% | 14% |
| TSLA, 09-21 | $0.40B | $0.36B | 39% | 19% |

Drawn as strike × time, that is premium appearing at a strike and then fading. A
reader would take the fade for money leaving. Nothing left; the options got cheaper.

**What an honest Premium view needs** is the increment the gate named: per contract,
`(volume now − volume a minute ago) × mark × 100`, summed per strike and side, and
stored as its own view. That is what traded in the minute at the price it traded
near. Its running sum is a true cumulative (it cannot fall) and a difference of two
readings is exactly the premium traded between them. The collector already keeps
each contract's previous volume in memory for the flow detectors, so the inputs
exist. It is a collector and storage change on the one-minute branch, forward-only,
and it wants its own design and its own cost measurement. It is not part of this
build.

**A finding outside this design.** The Flow view's ribbon and the Net Prem lines
plot the same stored quantity, summed over strikes, and describe it as cumulative
traded dollars. By the second table it is not: it falls in a third or more of
minutes and can end the day well under its peak. That is those pages' to weigh; it
is recorded here because this is where it was measured.

## 8. Phases

Each phase ships on its own and leaves the page whole.

| Phase | Ships | Tiers |
|---|---|---|
| 1 | Value (Net, Calls, Puts, Size), split bars, balanced markers, the legend on the adaptive scale | page |
| 2 | Scale: Locked (default) and Share of column | page + the `scale_lock` field |
| 3 | Frame: From spot | page + the wider crop + `n_side` to config |
| 4 | Show: Change | page |
| 5 | Value: Premium | page + the fifth history key, behind the measurement gate |

Phase 1 alone fixes the balanced strike and puts numbers on the colour. If the
work stops there the page is better than today.

## 9. What differs from the mock-up

- **"Gross" and "Sign + size" are one mode, Size.** The mock-up draws Sign + size
  with hue from net and brightness from gross, a balanced strike coming out
  near-white. The bundled Highcharts cannot colour an interpolated cell from two
  values (first row of "Facts that shape the design"). Size keeps the hue on the existing axis and covers the
  balanced strike with markers and split bars. A true two-value cell needs a
  second interpolated series with its own colour axis laid over the first. That
  is three unproven behaviours in one change (a second colour axis created with
  the element, an interpolated series bound to it, and the count moving from nine
  to ten), so it is left as a possible follow-up with a spike in front of it.
- **Change is its own control**, so it applies to every value.
- **The lock is the service's**, and Premium has no Locked scale.
- **The replay slider is not in this design.** It was in the mock-up to show the
  scale drifting. Clicking a column to rewind the bars is a separate feature.

## 10. Not in this design

Contours, the ridge plot, the terrain view, a regime strip, flow-alert pins,
multi-day stitching, a percentage offset axis for the spot frame, and a lock
anchored to the prior session's close (which would make one day comparable with
the next).

## 11. Tests and verification

**Pure builders** (`webgui/tests/test_gamma_heat.py`): each value on a cell with
a known call and put; Size on a balanced cell, a zero net and a bare-number cell;
`delta` with a strike absent at the basis; `scale_max` for each scale, with and
without a lock; `share_of_column` on an all-zero column; `to_spot_frame` on an
uneven ladder and on a column with no strikes on one side; the legend's tags
against the DOMPurify allow-list.

**Pinned behaviour that must not move:** nine series in every combination; the
stops list unchanged; `plotLines` emitted on every paint; `heatmap_categories`
matching the figure with the projection off; no transform writing to its input,
checked by handing each one a frozen structure.

**Service:** the lock is identical across two builds of one session, is absent
before `minutes` have passed, and is unaffected by the crop; the wider crop
contains the display window around the session's low and high; a degraded lock
build is counted through `_degrade.degraded`.

**In a browser.** There is no dev environment, so: the local page harness
(`tools/ui_harness.py`) on a fake bus with a stored session, every combination
of the four controls, on the private and the public render; then, after the
promote, a read-only check on prod that `scale_lock` is present and a screenshot
of each phase's default view.

**Documents in the same commits:** `webgui/page_help.py` (the page guide and the
four tooltips), the User Guide and Reference Guide entries for Dealer
Positioning, the Technical Reference (the Size, Change and lock formulas),
`docs/webgui-routes.md`, and the changelog. `CLAUDE.md` gains only
`gamma_heat` in its list of config files.

## Open before the plan

1. The unit line for the Charm, DEX and Vanna legends.
2. Whether Locked should be the default from the first day, or Adaptive until
   the lock has been watched for a week.
