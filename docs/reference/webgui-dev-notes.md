# Web tier development notes, NiceGUI gotchas and the sentiment scoring invariants

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

## webgui development notes (read before adding a page)

**Page pattern.** Add a leaf module `webgui/pages/<name>.py` exposing `render()`.
In `webgui/main.py`, add a `@ui.page("/route")` that does `with _layout(active,
title): from pages import <name>; <name>.render()`, and add the item to `NAV`
(flat) — `_layout` handles header, drawer, and the proxy-down banner. Register
the route in `test_shell.py`'s expected set.

**Printing a number (2026-10-04).** A price, strike, ratio, percentage or dollar
total prints exactly two decimals: `450.00`, never `450`. Say what the number IS
through `pages/fmt.py` — `price` (`6,712.81`), `strike` (`450.00`, no separator,
because it sits in `450.00/445.00` pairs), `ratio`, `pct`, `money`,
`money_short` (`$1.20M`) — and use `fmt.plain` for a count, a day count or a
score, which stay as they are. In a table, keep the NUMBER in the row and name
the column in `kit.table(decimals=(...))`: the browser prints it to two places
and the column still sorts numerically, where a cell formatted to text in Python
sorts `"1,000.00"` below `"999.00"`. ⚠ A `body-cell-<name>` slot receives the
FORMATTED text as `props.value`; a slot that does arithmetic or a null check
reads `props.row.<field>`. Not in the two-decimal family: the Greeks and model
statistics (more places), dealer exposure magnitudes, scale ticks on a dollar or
percent axis, CSS widths and `data-` attributes. `tests/test_two_decimals_guard.py`
fails on a `:g` format in any page module outside `fmt.py`.

**Import an app's engine (sys.path glue).** App folders have hyphens / no package
init, so a page adds the app dir to `sys.path` then imports the module by name —
e.g. `from repo_paths import TRADE_ANALYZER; sys.path.insert(0,
str(TRADE_ANALYZER))` then `import <engine>`. `webgui/conftest.py` already puts
the repo root + `webgui` on `sys.path` for tests. The proxy client is
`proxy.schwab_py_client` (schwab-py compatible) and `proxy.schwab_client`
(SchwabClient compatible).

**`shared/analysis_lib` is a LIBRARY of three modules, and that is now enforced
(2026-08-20).** It used to be the abandoned **"Blueprint Analyzer" Tk app** —
~9,600 of its 11,406 lines with no callers outside each other — and its
`__init__.py` eagerly imported all of them, **including a `schwab_client`
documented in-repo as broken**. That eager init is precisely *why* all four live
consumers (`sentiment_svc.compute`, `trade_svc.compute`, `scoring.regime_evidence`,
`portfolio-analyzer/src/sectors`) carry a `sys.path` bootstrap to import
`technical` **standalone** and dodge the package: a plain
`from shared.analysis_lib import technical` raised. The app is gone; the init
imports only `config`/`sector_analysis`/`technical`, and `technical` resolves
`config` **relatively when loaded as a package and by bare name when loaded
standalone** (`sector_analysis` no longer imports it at all) — branched on `__package__`, deliberately *not* a `try/except`, so a
real error inside `config.py` cannot fall through and silently bind another app's
`config` (the very collision this section is about). The bootstraps still work and
can go whenever their files are next touched.
`shared/tests/test_analysis_lib_surface.py` fails if the app grows back.

> **Cross-app module-name collisions (IMPORTANT, bitten us).** Putting multiple
> app dirs on `sys.path` means same-named top-level modules clash process-wide
> (one `sys.modules` entry wins). Known clashes: **`scoring`** (options-scanner
> `scoring.py` vs sentiment-dashboard `scoring/` package) and **`notifier`**.
> Once the Sentiment page loads, `import scoring` resolves to sentiment's, which
> broke `scanner_engine.run_full_scan`'s lazy `from scoring import …`. Mitigation:
> `pages/options/engines.py` `options_scoring()` context manager pins the options
> `scoring` for the duration of an options engine call and restores after — used
> in `scanner.py`/`swing.py`. When wiring a new service, watch for the
> same trap (e.g. `notifier`); prefer importing engine deps eagerly at module
> load (binds the name once) and/or wrap lazy engine calls similarly.

> **Stdlib collisions via the script-launch path (IMPORTANT, bitten us 2026-06-24).**
> A service's OWN dir lands on `sys.path` when its `app.py` runs **as a script**
> (`python services/<svc>/app.py`), so a module there named after a **Python stdlib
> module** shadows it process-wide. The (since removed) `driver_svc` once had a
> `secrets.py` API-key resolver that shadowed the stdlib `secrets`, so starlette's
> `from secrets import token_hex` (pulled in by FastAPI) crashed it **on launch** —
> but NOT in tests (pytest runs from the repo root, a different `sys.path`, so the
> suite was green while the service couldn't start). ⚠ The guard that checked every
> service module name against `sys.stdlib_module_names` lived in that service's
> tests and went with it on 2026-09-22. **Rule:** never name a
> service module after a stdlib module (`secrets`/`token`/`types`/`queue`/`select`/…).

**Structure for testability.** Keep pure transforms/figure-builders as
module-level functions (TDD them with sample dicts); keep `render()` thin
(widgets + wiring). Heavy/blocking engine calls go through
`await nicegui.run.io_bound(fn, ...)` with a spinner + try/except → `ui.notify`.

**NiceGUI gotchas (learned, costly):**
- `ui.html(...)` **strips `<style>` and `<iframe>`**. For CSS use `ui.add_css(css)`
  (rules only, scope with a class); render HTML *fragments*, not full documents.
  See `pages/options/gamma.py` Explain (`EXPLAIN_CSS`).
- **`ui.html` sanitizes through the BUNDLED DOMPurify, and its allow-list is
  READABLE — so a stripped attribute is a testable invariant, not a mystery
  (cost: every label on the new /sentiment rings silently mis-positioned, with a
  fully green suite).** `html.js` calls `setHTML`, but that is NOT the native
  API: NiceGUI monkeypatches it at `templates/index.html:144` —
  `Element.prototype.setHTML = function (html) { this.innerHTML =
  DOMPurify.sanitize(html); }`, its own comment explaining that native `setHTML`
  strips class attributes. So the effective allow-list is **DOMPurify's
  default**, which is laxer in some places and stricter in others. It allows
  `alignment-baseline` and `baseline-shift` but **NOT `dominant-baseline`** —
  the obvious spelling for vertically centring SVG `<text>`, and the one
  the ring dial's text builder shipped with. Client-side every label dropped to the alphabetic
  baseline while the **server-side string stayed correct**, so nothing in the
  suite could see it. Fixed with the pre-`dominant-baseline` idiom, `dy="0.35em"`
  (`rings._BASELINE_DY`), which is allow-listed and depends on no allow-list
  detail that can change under us; `sanitize=False` was considered and rejected
  as disproportionate for a dial. **The general fix is the test:**
  `webgui/tests/test_rings.py::_dompurify_allowlist` extracts the allow-list out
  of the shipped `nicegui/static/dompurify.mjs` (long runs of quoted lowercase
  tokens, **dropping any run containing `script`** — DOMPurify also ships DENY
  lists, and unioning those in blessed `<use>`), and every hand-drawn SVG
  builder's test (console dial, momentum, finder, persistence) asserts each tag
  and attribute it emits survives it. (The ring dial itself was retired with the
  console redesign and its builder removed 2026-09-19.)
- **`vector-effect` is NOT allow-listed either — which makes a scaled `viewBox`
  a trap for any line you draw (2026-08-17, caught twice before shipping).** The
  standard way to stretch a drawing across a fluid-width box is
  `viewBox="0 0 100 100"` + `preserveAspectRatio="none"`, and the standard way to
  stop that non-uniform scale smearing the stroke is
  `vector-effect: non-scaling-stroke`. DOMPurify strips it, so the geometry
  survives and the strokes render **thick horizontally and hairline vertically**
  — and, as ever, the server-side string stays perfectly correct, so nothing in
  the suite can see it. Both supplied designs the RRG and Momentum rebuilds came
  from used exactly this idiom. **The fix is to need neither: address the line
  endpoints in PERCENTAGES and drop the viewBox**, so percentages resolve against
  the viewport and `stroke-width` stays in real pixels (`rrg_view.tail_svg`,
  `momentum_view.rank_svg`). ⚠ `<polyline points="…">` **cannot** take
  percentages — that attribute is a list of user-space numbers — so a percentage
  layer has to be individual `<line>`s. Both builders carry the same
  allow-list test `rings.py` does.
- **`getBBox()` on an SVG `<text>` returns the EM box, not the ink — so it is the
  wrong tool for optical centring.** Centring the ring's value/caption pair on
  `getBBox()` left it visibly low: the box reported a 4.2px offset where the
  measured INK offset was **10.7px**, because an em box carries ascender and
  descender space that lining digits and all-caps captions never fill. Measure
  with canvas `actualBoundingBoxAscent`/`actualBoundingBoxDescent` instead. (The
  same reason `rings._BASELINE_DY` is 0.35em ≈ half the app font's cap height,
  and deliberately not a reproduction of `dominant-baseline:middle`, which
  centres on the *x*-height and so sits ~0.09em high for cap-height glyphs.)
- **A drawer's `.nav-drawer` class is NOT the `<aside>` — you cannot size the drawer
  through it (cost: hours; the CSS silently did nothing).** NiceGUI puts the classes
  you pass to `ui.left_drawer(...).classes(...)` on Quasar's **inner**
  `div.q-drawer__content.fit.scroll.nicegui-drawer.nav-drawer`; the **parent
  `<aside class="q-drawer">` is what carries the inline `style="width:…"`** written by
  the `width` prop. Styling the child resizes a CHILD of the width-holder → no visible
  effect. Reach the aside via **`:has(> .nav-drawer)`** (see `main._NAV_CSS`). Related,
  verified: `ui.left_drawer` has **no `width` kwarg** — width goes through
  `.props("width=64")`; and an unlayered author `!important` (what `ui.add_css` emits)
  **does** beat the aside's inline width, but it does **NOT** beat NiceGUI's
  `layer(quasar_importants)` rules (e.g. `.fit{width:100%!important}` — which is on the
  CONTENT div, and 100% of a 248px aside is what you want anyway). Know that asymmetry
  before fighting a width.
- **CSS transitions FREEZE in the automation browser, so `getComputedStyle`
  measurements LIE (cost: hours chasing phantom values).** The Claude Browser pane's
  tab is backgrounded (`document.visibilityState === "hidden"`), so
  `document.timeline.currentTime` stays **0 forever**. Every CSS transition sits
  `playState:"running"` at `currentTime: 0` indefinitely, pinning its property at its
  **START** value — and a running transition outranks even author `!important`. So a
  transitioned property reads its **pre-change** value forever: a phantom `width: 300px`
  while the inline style says `64px`; a label stuck at `opacity: 0` while its
  `opacity: 1` rule is present, matching, and higher-specificity. **The tell:** one
  property from a selector applies instantly while another property from the *identical*
  selector doesn't. **Fix before measuring:** inject
  `* { transition: none !important; animation: none !important; }`. (Related preview
  caveats: `computer{action:"screenshot"}` times out on this app — verify via DOM eval;
  and hover-by-`ref` works while hover-by-coordinate requires a screenshot first.)
- **A CSS animation on a row a painter REBUILDS restarts from zero, and
  `animation-fill-mode: forwards` then outranks every `hover:` rule you own
  (2026-08-21, both caught in review before shipping).** Two traps in one
  mechanism, both silent. (1) `_paint_positions` does `.clear()` + rebuild on
  every re-price, and a rebuilt element restarts its animation — so a
  time-limited effect never expires, which is indistinguishable from never
  having built it. The fix is a whole-second **negative `animation-delay`** so a
  rebuilt element RESUMES: ten static classes `desk-neon-0…9`, never a computed
  `[animation-delay:-3.2s]` (the finite-set rule). ⚠ Emit the base rule as
  `animation-name`/`-duration` **longhands** — the `animation:` shorthand
  declares `animation-delay: 0s`, and since both selectors are one class the
  winner is decided by nothing but source order in an f-string. (2) Per CSS
  Cascade §6.6.2 **animation declarations outrank normal author declarations**,
  so `forwards` holds the final keyframe forever and beats the row's
  `hover:bg-…` for as long as the class sits there — on a `cursor-pointer` row,
  for the rest of the session. Drop `forwards` when the end keyframe already IS
  the element's default. See `pages/desk.py` `DESK_NEON_CSS`.
- **A `@ui.page` function's SIGNATURE is handed to FastAPI, so any parameter — a
  bound default included — becomes a QUERY PARAMETER a stranger can set.** The
  idiomatic late-binding fix for a loop variable, `def _page(_s=screen)`, therefore
  publishes `_s`: measured while building the public live screens,
  `GET /desk?_s=anything` replaced the `Screen` object with the string `'anything'`,
  which reached an `importlib.import_module`. **Bind in an enclosing function's
  parameter instead** (`def _register(screen):` wrapping a zero-argument
  `@ui.page(...) def _page():`), so the page function has no signature to inject
  into. See `webgui/live_main.py._register`.
- **`ui.highchart` inside an inactive `ui.tab_panel` COLLAPSES (cost: the IV-shock
  bug).** The `nicegui-highcharts` Vue component reflows **once** at `mounted()` and
  has **NO ResizeObserver** (`update()` calls `chart.update()`, which does NOT resize
  to the container). A chart that mounts while its tab is hidden (`display:none`)
  measures a 0×0 container and renders collapsed (title-height, ~600px wide) and
  never recovers when the tab is shown. Fix: (a) give the figure an **explicit
  `chart.height`** (so it never depends on container measurement — the default-active
  Replay tab's chart already did, the hidden What-if/IV-shock didn't), AND (b) on
  `tabs.on_value_change`, **reflow** each chart after the panel is visible:
  `ui.timer(0.05, lambda: ui.run_javascript(f"getElement({el.id})?.chart?.reflow()"),
  once=True)` (`getElement(id)` → the Vue component; `.chart` is the Highcharts
  instance). See `pages/options/simulator.py`. ⚠ **A tab panel is not the only way
  to mount hidden — `set_visibility(False)` at build does it too**, and that one is
  easier to miss because the element looks unconditional in the layout code. The
  Gamma page's hedge-pressure panel is created then immediately hidden (a symbol
  with no 0-DTE book never shows it), so it mounted at **8px wide inside a 689px
  column** and stayed there once shown — a panel whose entire job is to read
  vertically against the heatmap above it. Any element that mounts hidden belongs
  in the page's reflow set, called AFTER the repaint's `set_visibility` (reflowing
  a hidden element just re-measures zero).
- Charts: **Highcharts** via `ui.highchart(options)` (the `nicegui-highcharts`
  element) — NOT Plotly. Build the options dict in a pure function so it's
  unit-testable; update in place with `el.options = fig; el.update()` (replaces the
  old `update_figure`). Heatmaps/bars in Gamma,
  line/column in Simulator, spline RRG in Sector Rotation, history line in Sentiment.
  **Gotchas (cost real time):** the `gauge` type auto-loads via `loadMore`, so pass
  NO `extras` — `extras=["highcharts-more"]` THROWS; `solid-gauge`/`heatmap` ARE
  valid explicit extras (both bundled). **Candlestick** is a Highcharts **Stock**
  series — pass `extras=["stock"]` (the `stock` module is bundled; it enables the
  candlestick/ohlc series + axis `crosshair.label` boxes). **Crosshair gotcha (cost
  hours):** the **datetime X-axis** `crosshair.label` box renders the RAW epoch-ms
  value and IGNORES both `label.format` (date tokens) AND a `label.formatter` function
  — verified on a plain `chart` AND `stockChart` (the formatter, shippable via NiceGUI's
  `:`-prefixed dynamic-property → `new Function`, IS attached + returns the right date
  but Highcharts never calls it). A NUMERIC y-axis crosshair label DOES honor `format`
  (e.g. `{value:.2f}`). So: keep the X crosshair LINE but disable its label box, show
  price on the Y label, put the DATE in the tooltip header (`tooltip.xDateFormat`) —
  see `pages/options/expected_move.py`. **`ui.highchart` accepts `type=`** ("chart"
  default / "stockChart" / "mapChart") → renders `Highcharts.stockChart` etc. (the
  JS does `Highcharts[this.type]`); `chart.update()` still applies in place. Use
  `type="stockChart"` for an **ordinal x-axis** that COLLAPSES non-trading-day gaps
  (weekends/holidays) in candlestick data automatically with NO calendar — but a
  forward series you generate yourself (e.g. the EM cone) must ALSO omit non-trading
  days or ordinal re-opens the gap (see `expected_move.py` + `compute.em_cone(...,
  trading_days_only=True)`). **The STOCK MODULE and in-place updates DON'T MIX — it is
  the MODULE, not `type="stockChart"` (2026-07-06 cost: the frozen sentiment intraday
  graphs; RE-DIAGNOSED 2026-07-28 at the cost of a failed Gamma candlestick attempt):**
  loading the stock module patches `Chart.update`, which then throws
  `Cannot read properties of undefined (reading 'enabled')` on a chart that lacks the
  stock scaffolding. The 2026-07-06 note blamed `type="stockChart"`; in fact **merely
  passing `extras=["stock"]` to a PLAIN chart is enough**, and the failure is worse
  there — the throw aborts the update mid-way and leaves the chart with **ZERO series**
  (a blank panel), not merely frozen. Live-verified on `/options/gamma`: with `stock`
  loaded the heatmap did not draw **even with the spot overlay set to a plain line**.
  Disabling `navigator`/`scrollbar`/`rangeSelector` did NOT help, and neither did
  pre-seeding every series at element creation (the trick that DOES work for
  `colorAxis`). **So: a PLAIN chart that repaints via `el.options = …;
  el.update()` must not load the stock module** — which rules out the
  `candlestick`/`ohlc`/`flags` series types on it. ⚠ **That sentence read "any
  element … can never" until 2026-09-20, and it was broader than the truth** — the
  same paragraph's own mechanism says the patched `Chart.update` throws *"on a chart
  that lacks the stock scaffolding"*, and a `type="stockChart"` element HAS it.
  Measured in the harness on the bundled Highcharts 12, on the Expected Move page
  (`extras=["stock"]` + `type="stockChart"` + in-place `update()`, which the blanket
  wording forbade): `update()` did **not** throw and all three series survived —
  1 → 3, candlestick + two splines. A plain `Highcharts.chart` built on that same
  page, with the stock module loaded, also updated cleanly 1 → 2 series. ⚠ **That
  does NOT clear the gamma case**, which is the one that actually failed live: its
  heatmap carries a `colorAxis` and the note below records that pre-seeding every
  series did not rescue it. Treat the gamma prohibition as standing and unmeasured
  until someone repeats this probe on a heatmap. Draw bars from CORE series instead: a `columnrange` body +
  an `errorbar` wick, each point carrying its own `color` (see
  `gamma.candle_points` — one series then holds both up and down bars). `columnrange`
  /`errorbar` need **no `extras` at all** (the `more` module auto-loads, same as
  `gauge`), so nothing patches `update`. For a NON-updating chart, `type="stockChart"`
  remains fine (see the Expected Move page); pack time gaps with a synthetic category
  axis instead (`xAxis.breaks` is no substitute — it renders zero ticks). A
  `ui.highchart` added DYNAMICALLY on a page
  with no chart at first render fails `Failed to resolve module specifier
  nicegui-highcharts` (the ESM import map is set at initial render) — keep a chart
  present at page build (e.g. a persistent element, as `detail.py` does). A
  Highcharts `bar` reverses its xAxis by default (`reversed:False` = high values at
  top). `chart.update()` MERGES options, so a series-TYPE switch leaks the old type's
  plotLines/colorAxis — RECREATE the element on kind-change (bar↔heatmap), don't
  update in place (see `gamma._set_chart`). A **green-above-zero / red-below-zero P&L
  payoff** = an `area` series with `threshold:0` + `color`/`negativeColor` (line) +
  `fillColor`/`negativeFillColor` (fill); you MUST set an explicit base `color`/
  `fillColor` or Highcharts paints a default-blue (`#2caffe`) base path UNDER the
  green/red split (cost: a stray blue area — see `simulator.whatif_figure`).
  `accessibility.enabled:False` silences
  the a11y-module console nag (house pattern). (`plotly` was never a Python dep —
  `ui.plotly(dict)` rendered via bundled plotly.js.)
- **Blended heatmap + colorAxis alpha:** `series.interpolation:True` (Highcharts 12,
  in the bundled `heatmap` module) renders the heatmap as ONE smooth interpolated
  `<image>` (no per-cell `<rect>`s, so no borders/separator mesh) — the "blended"
  look. **colorAxis `stops` honor rgba alpha** through the interpolated image, so a
  `rgba(...,0)` stop at the zero-point makes net≈0 fade to transparent and the dark
  page shows through (`gamma.HEAT_STOPS` + `chart.backgroundColor:"transparent"`).
  Set `borderWidth:0`. **`states:{inactive:
  {enabled:False},hover:{enabled:False}}`** stops the hover-dim/fade.
  **`plotBackgroundColor` is NOT banned here — that instruction was narrower than it
  read, and it was corrected 2026-08-15.** This line said "drop `plotBackgroundColor`
  (the mesh)" because at the time (commit `e6ef342`) it held a FLAT grey
  (`HEATMAP_SEP`) that showed through the gaps between individually-bordered cells
  and read as a separator mesh. `interpolation: True` renders ONE continuous image
  with no cell gaps at all, so nothing can show BETWEEN cells any more — a fill here
  is a wash painted BEHIND the image. `gamma._wash_background()` uses exactly that
  for the plasma blue→magenta wash. What must not come back is a **flat opaque**
  plot background, which would defeat the `rgba(...,0)` zero stop by putting a solid
  colour where the page used to show through.
- **A series drawn in the ORDER of its points must not be updated in place
  (2026-10-10, cost: the heatmap's contour lines, reported by the user the day they
  shipped).** On `chart.update()` Highcharts does not replace a series' data. With
  old and new data both non-empty, `Series.setData` calls `updateData`, which
  matches each new point to an old one BY X, updates the matches where they stand,
  removes the unmatched old points and APPENDS the unmatched new ones. A `line` or
  `area` series is kept sorted by x, so nobody notices. A `scatter` series is not,
  and a scatter with a `lineWidth` (used because a contour doubles back in time,
  which a `line` series cannot draw) joins its points in whatever order they end up
  in: the contours came out linked by long straight strokes. Empty to non-empty is
  a clean build, which is why turning the switch off and on cured it, and why a
  check that only turns a switch ON does not find it. **The fix:** `Series.update`
  rebuilds a series, instead of keeping its points, when its `pointStart` (or
  `keys`, `pointInterval`) changes. `pointStart` means nothing to points that carry
  their own x, so each contour series sets it to `gamma_heat.stamp(points)`, a
  CRC-32 of the points: the same lines keep it, changed lines move it. Read from the
  bundled build (`nicegui_highcharts/dist/index-*.js`, `update(t,e)` and
  `updateData`), and proven in the page harness by comparing each series' drawn
  points with the data sent, across three changes of the lines. ⚠ It leans on what
  that build does, so re-run that comparison after a NiceGUI upgrade. The
  chart-wide switch `chart.allowMutatingData = false` turns the matching off for
  every series and was not used: it changes how the heatmap's own cells update.
  To check any chart: compare `getElement(id).chart.series[k].points` with
  `getElement(id).options.series[k].data` after the data has changed TWICE.
- **An interpolated heatmap needs a UNIFORM data grid, or it combs (2026-08-11, cost:
  a long misdiagnosis).** `interpolation:True` rasterizes onto a canvas laid out on ONE
  row height — `rowsize`, which `gamma._strike_step` derives as the MEDIAN strike gap.
  Feed it a chain whose strikes are unevenly spaced and the finer strikes collide
  two-into-one canvas row while the cells between them are never written; upscaled, that
  reads as a **comb of vertical stripes**, not a smooth field. **$NDX is the only symbol
  in this app that hits it** — it quotes **5-wide near the money among 10-wide** (measured
  live: 28 gaps of 5 among 56 of 10), where $SPX is uniformly 5, SPY/QQQ/IWM 1, AMD 2.5.
  Fix = `gamma.uniform_strike_grid(strikes, z)`: fill the ladder to the FINEST gap,
  linearly interpolating inserted rows between their bracketing real strikes (it invents
  nothing the chart wasn't already implying — an interpolated heatmap shades between
  samples regardless; it just does it on a grid the rasterizer can represent). Real
  strikes pass through untouched, a row bracketed by a missing sample stays `None` so
  genuine holes stay holes, an already-even ladder returns the SAME objects (no cost),
  and `_MAX_UNIFORM_ROWS`=240 stops one stray half-strike exploding the window. Applied
  to the collected cells AND the projection band; run on the VISIBLE strikes only (the
  full chain spans ~3000–9800 with wide wing gaps → the cap would refuse it).
  **The Term heatmap is immune** — its axes are CATEGORICAL and points are addressed by
  row INDEX, so an uneven ladder cannot collide rows (the trade-off being that its y axis
  is ordinal, not proportional to price). **Diagnosing this class:** the tell is *smooth
  stored values under striped pixels* — read the series out of the cache and compare it
  to the rendered PNG's per-column alpha (`canvas.getImageData`). Rule out the overlay
  series first by hiding them in the DOM; a full-rectangle source grid plus a striped
  image means the rasterizer, never the data.
- **Tooltip ONLY on press-and-hold (or click), not hover** (the `nicegui-highcharts`
  way): you can't do it purely in config, and the component **clobbers**
  `plotOptions.series.point.events.click` (it wires its own `pointClick` `$emit`). The
  trick (`gamma._HEAT_PRESS_TOOLTIP_JS`, shipped as a `:`-dynamic `chart.events.load`
  function): monkeypatch `chart.tooltip.refresh` to a gated no-op, then a container
  `mousedown` opens the gate + `runPointActions` shows the point under the cursor
  (Highcharts' own mousemove keeps it following while held), and a `document`
  `mouseup` closes the gate + `tooltip.hide(0)`. **Gotcha:** `chart.events.load` fires
  ONCE at element creation, and a persistent `ui.highchart` (created with one fig,
  then `el.options=…` BEFORE the client mounts) mounts with the LAST-set options — so
  the load hook must be on the figure the element actually mounts with (carry it in
  the figure BUILDER, e.g. `heatmap_figure`/`term_heatmap`, not just the init fig).
  Don't re-set the global `tooltip`/`chart.events` on in-place updates or you rebuild
  the tooltip and lose the runtime monkeypatch.
- **`ui.slider` keeps `min`/`max`/`step` in `_props`, so `slider.max = n` does NOTHING in the browser** — it sets a plain Python attribute. The Simulator's Replay scrubber sat on bars 0–1 for months that way, with a green suite. For a range that changes at runtime write `slider._props["max"] = n` then `slider.update()`; not `.props("max=n")`, whose parser sends the number as a STRING.
- **A server-paged `ui.table` announces its own pagination when it mounts, and
  NiceGUI writes it over the element's (2026-10-04, found in a browser, not by
  the suite).** The Market Scanner's first paint lands 50 ms after build, so the
  table's `update:pagination` (rowsNumber 0) arrived after it and the footer read
  "1-0 of 0" over a hundred rows. Keep the pagination the page last SENT in page
  state, never read it back from `table.pagination`, and put it back in an
  `on_pagination_change` handler. The pager is `kit.page_of`.
- Tables: `ui.table(columns=[{name,label,field,...}], rows=[...], row_key="id")`;
  selection via `selection="single"` + `table.selected`; row click via
  `table.on("rowClick", handler)` where `event.args[1]` is the row dict.
- Number/select/slider/toggle fire `on_value_change`. Set values with
  `el.value = ...; el.update()`. Auto-refresh + autoload via `ui.timer(secs, fn)`
  and `ui.timer(0.1, fn, once=True)` (see Gamma).
- A page is built per request inside `_layout`; keep page state in a local dict
  closure, not module globals.

**Verify in the browser.** `.claude/launch.json` defines TWO configurations
(`autoPort:false` — the NiceGUI port is fixed, so the entry must match what the
checkout will actually bind): **`webgui-dev` on :9500** and **`webgui` on :8500**.
**Pick the one matching the checkout you are in** — `repo_paths` decides the real
port from `config/env.local.toml`, and the launcher only *probes* the port named
here. ⚠ **A worktree has no `env.local.toml`, so it resolves to PROD and binds
:8500** — where the live prod stack already is. Either drop a
`name = "dev"` marker in the worktree first (it is gitignored, so it cannot
travel) or do not preview from a worktree at all.

**Three restart traps, all hit on 2026-08-17.** (1) **A failed bind is silent.**
If something already holds the port, the new server exits and the OLD one keeps
serving — so you verify stale code while everything looks healthy. The tell is in
the launcher log: `[Errno 10048] error while attempting to bind`. **Always
confirm the port is actually free after killing, and read that log.** (2)
**`netstat`-and-taskkill one-liners are easy to get wrong**: a `$`-anchored regex
inside a double-quoted `-c "…"` string has its anchor eaten by the shell, so the
kill matches nothing and cheerfully reports success. Prefer
`Get-NetTCPConnection -LocalPort N -State Listen`. (3) **Dev serves the DEV
CHECKOUT, not your worktree** — uncommitted worktree changes are invisible there,
which reads exactly like a broken restart. Commit and fast-forward first.

Use the Claude Preview tool (start the right config, screenshot). Restart the
preview after code changes to pick them up. To drive Quasar inputs from the preview, set the native value +
dispatch `input`/`change`/`blur` events. **Caveats (seen):** the **screenshot**
tool TIMES OUT on heavy multi-panel Highcharts pages (e.g. the Replay 6-panel
stack) — it works on lighter single-chart pages (EM); when it hangs, verify via
DOM `preview_eval` (read `.highcharts-series`/axis geometry) instead. A **Quasar
`q-slider` can't be driven by synthetic mouse/pointer/keyboard events** (no native
input) — assert slider→handler wiring with a unit test, not the preview. For
3-tier pages, the most reliable end-to-end check is **Redis-driven**: enqueue a
command with `Bus().enqueue_command("cmd:<domain>", {...})` and read the result
with `Bus().cache_get("cache:<domain>:<view>")` — bypasses the browser entirely.
Service code changes require **restarting that service** (the running one is
stale); the proxy's REST market data works even when `/health` shows
`token_expired:true` (auto-refresh) — only a missing/expired **refresh** token is
fatal.

**Tests:** `(cd webgui && ../.venv/bin/python -m pytest -q)`. The dated baseline, the
standing warning about comparing the failing *set* rather than the count, and the
worktree/subshell caveat all live in the **Tests** section — don't duplicate a count here.
TDD pure functions; smoke-verify `render()` with a screenshot.
`tests/test_no_inline_style.py` guards every migrated page against `.style(`/`:style=`
(the Tailwind-first standard) — add any new page to it.

**Environment quirks to expect:**
- The proxy on `:8100` may be the *source* repo's proxy (its `/health`
  `token_file` points at `D:\Trading With Schwab\...`). Fine for reading data;
  just know live data isn't this repo's proxy.
- Weekend / off-hours → sparse 0-DTE option data (e.g. "no non-zero GEX within
  ±2%", swing scans returning 0). Not a bug.
- `options-scanner/data/Top 20.xlsx` (scanner watchlist) is present locally but
  **gitignored** (`data/`), like the real secrets — a fresh clone degrades to
  base symbols. Same applies to the paper-trading DBs (start empty).
- **Proxy `/pricehistory` (fixed 2026-06-19):** `schwab_proxy.get_price_history`
  now calls `/pricehistory?symbol=…` directly (symbol is a query param). The old
  code tried `/{symbol}/pricehistory` first — a guaranteed 404 that `api_request`
  retried `MAX_RETRIES`× with backoff, flooding `errors.log` (~99% of all ERRORs)
  and wasting ~0.75 s/fetch. If you see a `D:\Trading With Schwab` source-repo proxy
  still on `:8100`, it may not have this fix.
- **`schwab_client.get_quotes` returns a FLATTENED mapping, not Schwab's raw envelope**
  — `{symbol: {"last", "change", "change_pct", "high", "low", "volume"}}`
  (`schwab-proxy/proxy_client.py`), with **no nested `"quote"` key**. Reading
  `q["quote"]["netPercentChange"]` yields `None` for *every* symbol with no exception
  and no empty render, and a unit test written against an invented fixture will pass
  while the live column is entirely blank. ⚠ `_extract_change_pct` also falls through
  to a literal **`0.0`** when every percent field is missing or zero, so a `0.00%` cell
  is not proof of a flat tape — only an OMITTED symbol is a real absence.

**Feature history lives in [docs/CHANGELOG.md](../CHANGELOG.md), not here.** The
per-feature build narratives that used to sit in this section — what shipped, the pieces,
the test counts at the time, the live-verification logs — were moved there on 2026-08-16.
Per-page detail moved to [docs/webgui-routes.md](../webgui-routes.md). Each feature also
has its own design/plan pair under `docs/plans/`. **Add new work to those, not to this file**
— see the maintenance banner at the top.

**A NaN clamps to the HIGH bound, so "no reading" renders as an EXTREME reading —
the trap behind FIVE separate bugs, across two tiers.** Every
`scoring/*` module defines its own private `_clamp(v, lo, hi) = max(lo, min(hi, v))`,
and `min(hi, nan)` returns **`hi`**. So an unguarded non-finite indicator does not
degrade — it pins the maximum. In `sentiment_svc/compute.py` it bit the SECTOR
sub-score first
(`score_sector_participation(5, 11, nan)` → 67.27 at confidence **1.0**, maximum
cyclical leadership), fixed by `_finite_pcts`; then the PRICE sub-score, where an
all-NaN read scored **92.50 at confidence 1.0** through `compute_intraday_trend`
(the LIVE Day gauge) and **82.50 at an unchanged 0.333** through `_structural_trend`
— a data outage rendering as a confident buy signal, fixed 2026-08-17 by
**`_finite_score_price`**, the single guarded wrapper BOTH price call sites go
through. Both filters share `_as_finite`.

**An audit on 2026-08-20 found the same trap still open in FOUR more scorers**, so
treat the guarded list as evidence of where someone has looked, never as evidence
that the surface is covered. Two distinct holes, each fixed at its own layer:
- **`_num` was not the guard it looked like.** `effort`, `rejection_defense` and
  `session_structure` each define a `_num` whose contract is "a usable number or
  `None`" — but they only caught `TypeError`/`ValueError`, so a **NaN passed
  straight through** while a `None` was caught. One NaN volume in a 30-day tail
  pinned effort's `updown_vol` **0.0039 → 1.0** (maximum motivated buying) at
  **unchanged confidence 1.0**. The sibling `order_flow._num` had rejected
  non-finites all along; the three now match it. This is completing a parsing
  guard, **not** the `_clamp` shortcut banned below.
- **A second-pass audit the same evening found the trap in THREE more places**,
  which is the strongest argument yet for treating the guarded list as a map of
  where someone has looked: `flow_skew._as_float` passed NaN (a NaN delta seeded
  `best_dist = nan`, and every later `dist < nan` is False — the NaN contract's
  IV won the 25Δ pick permanently) and accepted Schwab's **`volatility = -999`**
  sentinel as a usable IV; and `profile_shape._num` let one NaN volume flip a
  profile's shape and inflate `balance_strength` **15×**. Same fix as the
  siblings. `detect_uoa` turned out to be accidentally safe — NaN passes its
  floors but `int(nan)` raises into the broad `except` — and that accident is
  now pinned by tests so it cannot be un-fixed silently.
- **`is not None` does not mean "present", and neither does `or default`.**
  `blend_trend`'s `scores.get(k, 50.0) or 50.0` replaced a score of exactly
  **0.0** with neutral 50 — and 0.0 is not a corner: `score_price` clamps to
  [0,100], so the ENTIRE saturated crash-tape region lands on it. The most
  bearish possible tape blended **36.5** where one tick off the floor blended
  14.0, at confidence 1.0 (fixed 2026-08-20 evening; only absence means
  neutral). `intraday_trend`'s scorers each declare
  their own missing-input policy (drop the component, or return confidence 0) and
  tested it with `x is not None` / `not x` — both of which a NaN survives.
  Measured: `score_vix_context(nan, …)` returned **70.0 at confidence 1.0**, a
  confidently bullish trend read from no data, and `score_breadth_dir(nan, …)`
  returned **100.0**, maximum bullish breadth. They now run inputs through a local
  `_finite()` first, which extends each function's OWN stated policy rather than
  overriding a caller's intent.
- **A TOTAL function has no absence branch, so a default input picks its bottom
  band — and the guard was on the WRONG SIDE of the boundary (2026-08-24).**
  `live_composite.signal_band` maps a composite total to
  `(size, bias, signal)` through `>=9 / >=7 / >=5 / >=3 / else`. It is total
  over the reals, so `_safe_float`'s **0.0** default falls out of the last
  return — and so does a NaN, which fails every `>=` — publishing
  **`0.70x` / `Short` / `Strong Bear`**, the most bearish word in either
  vocabulary at the smallest position size, for six distinct ways of having no
  composite at all. Reached the Desk strip, the Market Regime Console, **and the
  market snapshot pushed to your phone**. Fixed at the call site in
  `sentiment_svc.compute.derive_composite_extras`, which publishes
  `None, None, None` when **`live_composite.composite_reading`** says there is
  nothing to band. ⚠ That test was `_as_finite` until 2026-10-03, and it missed
  the one shape the producer really emits with every fetch failing: the STRING
  `"0.00"` at aggregate confidence 0.0 — finite, so it still banded as Strong
  Bear, and the bridge wrote `strong_bearish`, which the scanner's regime filter
  counts as a bear vote. `composite_reading` (a finite total ABOVE zero at
  non-zero confidence) is now the one test, used by the snapshot, the bridge
  (which writes `unknown` and a null score) and the service; velocity is
  withheld with it. The producer publishes `total_score: None` for that case, so
  no screen draws 0.00 for it; `_composite_gate` accepts that one shape and still
  raises on a missing or junk total.
  ⚠ **The absence value has to be the one the consumers already test** — `None`
  here, since two of the three gate on `size is not None` and an empty-string
  triple is a truthy tuple that renders blank rather than dashed.
  **The lesson is where the guard lived.** All three renderers handled the
  absence correctly and none of them could ever fire, because the producer never
  emitted the shape they tested; `test_signal_band_facts_print_a_dash_for_a_cold_cache_never_neutral`
  asserts precisely the right invariant and passed throughout, by feeding a
  payload the service does not write. **A consumer-side guard proves nothing
  until a test drives it from the PRODUCER** — which is the same reason
  characterization tests pinned the ADX bug below.

**Four sentiment invariants from the 2026-10 audit, each of which moved live
numbers when it landed.** (1) The three volatility scorers (`scoring/vix.py`)
never rise as volatility rises; two calm-band segments in each sloped the wrong
way. (2) `sectors_score` returns `None` for no data and 1..10 for a real day (a
crash day is 1.0, a flat tape 5.0); the history backfill drops a day only on
`None`. (3) The Week and Month gauges call `score_price` with `None` for VWAP
(the term is left out and the direction renormalised) and
`expected_timeframes=1`, so their price score is a full reading rather than a
third of one. (4) Velocity, the regime-break flag, the bridge's rolling averages
and `derived.prev_total` compare the live composite with **live closes**
(`composite_close` in the intraday store, `handlers._live_closes`), never with
the stored history, which is scored by a different method; with no live history
there is no velocity.

**Do not "fix" this in `_clamp` itself** — it looks like the one-line cure and is
not. `_clamp` is **duplicated nine times** across `sentiment-dashboard/scoring/`
(`intraday_trend`, `aggression`, `effort`, `daily_direction`, `market_regime`,
`order_flow`, `profile_shape`, `rejection_defense`, `session_structure`), so
patching one covers a ninth of the surface; and there is no single right answer
inside the primitive — a NaN reaching `_clamp(50 + 50*direction, 0, 100)` means
"neutral 50", reaching `_clamp(adx/40, 0.3, 1.0)` means "floor the magnitude", and
reaching `_clamp(n_timeframes/3, 0, 1)` means "confidence 0", not the midpoint 0.5.
**Only the caller knows what a missing input implies**, which is why the guard
belongs at the call site. When you add a new scorer call, filter its inputs there.

**`shared/analysis_lib/technical.calculate_adx` was wrong for years, and a
characterization test pinned the wrong value in place (fixed 2026-08-20).** Its
`-DM` was built from `|delta_low|` rather than the signed `-delta_low`, so a
*rising* low was booked as downward movement; the direction gate `minus_dm < 0`
was tautological; and the `-DM` branch compared against an **already-filtered**
`plus_dm` (the line above reassigned it), so a tie zeroed the up-move and then
handed the bar to the down side. Measured on a realistic mixed tape: **76.58 vs a
textbook 32.5**. A dead-flat series read **ADX 100** — maximum trend strength —
which is why the regime classifier leaned Trending on exactly the tapes that are
not trending. Clean one-sided trends coincidentally still read ~100 (the DI sides
invert but the magnitude survives), which is how it went unnoticed.

**Two lessons worth more than the fix.** (1) `test_adx_uses_wilder_smoothing`
pinned `47.0052` — the buggy output — because a characterization test records
what the code *does*, and only a test written against an INDEPENDENT reference
records what it *should*. The replacements are property-based (a flat tape is not
a trend; mirroring a series cannot change trend STRENGTH) plus an equivalence
against `trade_svc.compute._adx_series`, which had the formula right the whole
time. (2) The 2026-07-01 audit closed a finding claiming "+DM/-DM rule & DX were
already correct; only smoothing changed" — that claim was simply false, and it
bought three more weeks of a wrong needle. ⚠ The two implementations **seed
differently on purpose** (`calculate_adx` drops the leading NaN and uses an SMA
seed, the TOS/TradingView convention; `_adx_series` uses `ewm`'s own warmup), so
they agree only once warmup has decayed — identical at 250 bars, 0.70 apart at 60.
Compare them on a long series or not at all.

**⚠ There are TWO RRG implementations with DIFFERENT momentum definitions, and
only one of them was wrong (found + half-fixed 2026-08-20).** Know which you are
looking at before touching either:

| engine | momentum formula | feeds |
|---|---|---|
| `scoring/rotation.compute_rrg_quadrants` | `100 · RS_today / RS_(20 bars ago)` — a rate; always was correct | `/sentiment/sectors`, `/sentiment/momentum` (via `compute.py`'s sector/industry builders) |
| `sector_rotation_assessment.compute_rs_momentum` | `100 + ROC / rolling_std(ROC)` — **was** `(ROC − mean(ROC)) / std` | `/sentiment/rrg`, `/sentiment/rotation` (via `rotation_assessment()` → `cache:sentiment:rotation`) |

The second subtracted ROC's **own rolling mean** before normalizing, which
differentiates twice: it measured the ACCELERATION of relative strength, not its
rate. Isolated on a controlled RS-Ratio series the sign came out **inverted** — a
steadily rising ratio read 99.70 ("weakening"), a steadily falling one 100.96
("strengthening"). Fixed by dropping that term.

⚠ **The real-data impact was far smaller than that inversion suggests, and the
synthetic evidence that first exposed it was misleading.** Deterministic ramp
series have degenerate rolling statistics; measured against two years of live SPY
+ the eleven sector ETFs, the old and new formulas agreed on **10 of 11 sector
quadrants** and on the risk-on/risk-off headline for **91% of sessions**, and
**neither** correlated with forward 20-bar excess return (−0.06 vs −0.04). Treat
it as a correctness-and-meaning fix, never as an edge. **If you are ever tempted
to characterise a rotation change from synthetic series, don't — fetch real bars
through the proxy.** One live consequence: the corrected spread has a slightly
wider tail (|spread| p90 1.35 → 1.51), so `RISK_THRESHOLD` (±1.5) now fires on
~10% of sessions where it used to fire on ~6%.

**⚠ Known open issue — Sector & Industry and Sector Rotation can print OPPOSITE
regime verdicts (not fixed; found 2026-08-17).** The two adjacent tabs each
render a "risk-on / risk-off" headline from a **different quantity on a different
scale**, so agreement is coincidence rather than design:

| | reads | scale |
|---|---|---|
| `/sentiment/sectors` (`sector_heat.regime_headline`) | `sector["rotation"]["day_spread"]` — cyclical minus defensive **daily % return** | bands ±0.3 / ±1.0 |
| `/sentiment/rotation` (`rotation_view.regime_display`) | `assessment.headline.spread` — mean **RS-momentum** spread from the RRG engine | the service's `risk_threshold`, ±1.5 |

Measured live on 2026-08-17: `day_spread` **+0.37** rendered "Risk-on regime" on
Sector & Industry while the RRG spread of **−1.52** rendered "Risk-off" on Sector
Rotation. **This predates the 2026-08-17 rebuilds** — the old sectors table's
`rotation_banner` had exactly the same split — so the rebuild inherited it rather
than caused it. ⚠ **Do NOT "align the thresholds": the two numbers are not
commensurable.** A real fix is a product decision — either the sectors header
reads the same `assessment.headline` the rotation page does, or its line stops
calling itself a *regime* and says what it actually measures (today's
cyclical-vs-defensive return spread). **`/sentiment/bullbear` is the standing
precedent for a new screen: it declines to add a third headline and counts its own
rows instead** ("5 of 11 sectors rising and leading"), and never reads
`payload["regime"]` — a reader may disagree with what a count implies, but not with
the count. Compare the Market Regime direction axis
below, which solves the same class of problem by naming a direction only when two
independent reads agree.

**Market Regime — display names + the direction axis (2026-08-14).** The five
regimes were renamed **for display only** and gained a direction word. **The
internal KEYS are unchanged** (`mean_reversion`/`trending`/`breakout`/`choppy`/
`crisis`) — they are the `RegimeState` contract and the `regime_intraday` DB
columns, so renaming them would be a migration with no
user-visible benefit. Only the words moved:

| key | was | now | why |
|---|---|---|---|
| `mean_reversion` | Mean Reversion | **Balanced** | Its five inputs (low ADX, flat EMA, mid-band width, balanced profile, above the gamma flip) all say price is **AT** the mean. Nothing measures an extreme, so the old name promised a fade the model never tested — and it was the only name naming a *strategy* rather than the tape. |
| `choppy` | Choppy | **Whipsaw** | Same "not trending" axis as Balanced; the distinguishing feature is ENERGY (high ATR + low ADX, failed breaks, two-sided wicks). Balanced/Whipsaw carries that contrast; Mean Reversion/Choppy did not. |
| `crisis` | Volatile | **Stressed** | `VIX_STRESS_LO` is 22 and the fast-attack fires near VIX 30 — stress, not crisis; and "Volatile" also describes breakout/whipsaw days. The distinctive evidence is FEAR (VIX level/spike, term inversion, unfilled gap, deep below flip). |

**Direction (`market_regime.direction_sign`/`commit_direction`/`regime_label`).**
`trending` and `breakout` now render **Rallying/Firming** (up), **Retreating/
Softening** (down) and **Breakdown**; Balanced/Whipsaw/Stressed are directionless
by construction. The **intensity math stays sign-blind** (`ramp(abs(slope),…)`)
— "is this a trend day" is answered identically up or down — so this is a
five-member simplex with a label adornment, **NOT** a sixth regime: splitting
`trending` would need a DB column, a chart series, a contract change, and would
tear the membership across two bins when the slope flips mid-session, defeating
the blended model's whole point.

**How the contradiction risk is avoided (the load-bearing part).** The app has
TWO direction reads: this module's signed `ema_slope_atr` (SPY price, 5-min) and
the Market Trend composite score (price+breadth+sector+VIX, 15-min, hysteresis-
committed). They diverge on a real condition — index up on narrow leadership
while breadth is negative — so a word from either alone can contradict the other
panel. `direction_sign` names a direction **only when both agree past their
deadbands** (`DIRECTION_SLOPE_DEADBAND` = `EMA_TREND_LO`; `DIRECTION_TREND_DEADBAND`
= 3 points either side of 50); otherwise the neutral base label renders, which is
exactly the pre-2026-08-14 behaviour. `handlers._committed_trend_score()` reads
the SAME `smoothed_score` the gauge renders and is taken **before** `_REGIME_LOCK`
(so `_TREND_LOCK` is never nested inside it). `commit_direction` is deliberately
**asymmetric** — two consecutive reads to CLAIM a direction, one to drop back to
neutral: never keep asserting a direction the evidence stopped backing.

**Two rendering rules that are easy to get wrong.** (1) The stacked-area **series
names stay the BASE words** — the fixed order + stable names ARE the band's
reading position, so a legend that renames itself intra-session defeats it;
direction belongs on the headline + transition line only. (2) The headline
**colour follows the direction** for the two directional regimes
(`_DIRECTION_TEXT`), because the fixed green would paint "Retreating" as though
it were bullish. The label is also **re-derived page-side** from
`(committed_label, direction)` rather than echoing the payload's `label`, so a
held sample can't outlive a rename — but an `unclear` sample short-circuits to
"Unclear" regardless of the held key. The words are **duplicated across tiers**
(`scoring/market_regime.REGIME_DISPLAY` is the source; `webgui/pages/sentiment.py`
and `options_svc/market_snapshot.py` mirror it) because
none of those may import that package — Tier-1 takes no engine imports and the
services would hit the documented cross-app `scoring` collision. Keep them in
step. The push snapshot's transition line also stopped rendering RAW KEYS
("mean_reversion → trending") at the same time.
