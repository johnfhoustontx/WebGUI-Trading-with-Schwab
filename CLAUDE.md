# CLAUDE.md — WebGUI Trading with Schwab

Guidance for Claude Code sessions working in this repository. Read this first,
then the per-app `CLAUDE.md` for the folder you are editing.

> **Maintenance:** This document is the living architecture/tech record for the
> project and is **updated regularly** as the build progresses (an explicit
> standing requirement). After any structural change — new page, new dependency,
> port change, copied/removed module — update the relevant section here.

**Last updated:** 2026-06-28 (**UI styling standard — Tailwind-first adopted**: all
NiceGUI component styling must use **Tailwind utility classes via `.classes()`** —
`.style()`/inline-style/fixed-px are banned; the dark-navy theme becomes **Python
Tailwind-class-string token constants** in `pages/options/theme.py` applied with
`.classes(CARD)`; a single `ui.add_css` escape hatch remains **only** for
Quasar-internal/teleported DOM (`q-field__control`/`q-tab*`/`.strat-menu-navy`);
standalone `HTMLResponse` docs + Highcharts dicts are out of scope. Scope **pragmatic**,
intent **convert + light polish**. Phased migration (menu first, then each screen by
logical group) — see the "UI styling standard — Tailwind-first" section below + the
[design doc](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md) /
[plan](docs/plans/2026-06-28-tailwind-first-ui-migration-plan.md) /
[phase2](docs/plans/2026-06-28-tailwind-first-ui-migration-phase2-plan.md) /
[phase3a](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3a-plan.md) /
[phase3b](docs/plans/2026-06-28-tailwind-first-ui-migration-phase3b-plan.md). **Phase 0
(token vocabulary) + Phase 1 (nav shell) + Phase 2 (shared `pages/options/*` helpers —
`.style()`-free, dynamic colors palette-mapped) + Phase 3a (the six signal-table screens
— Scanner/Swing/Captured/Paper/Paper-Portfolio/Rescue) + Phase 3b (Calculator + Simulator
on tokens; `DASHBOARD_CSS` now consumed ONLY by Trade) DONE** — webgui 581 green +
live-verified. **Phase 3a** removes every `.style()` AND every Vue `:style=` slot binding
from those pages: dynamic Quasar table-cell colors now map to a stamped Tailwind **`:class`**
field from a finite palette (`score_zone_class`/`rec_class`/`pnl_class`/`verdict_class`/
`heat_bg_class`/`cash_class` — exact hexes preserved as `bg-[#..]`/`text-[#..]` arbitrary
classes; the shared `heat_border_class` in `rescue.py` is imported by captured/paper/portfolio
for the at-risk left-border tint, DRY); the 3D gradient buttons (Scanner "Run scan" + Paper
actions) move to shared **`theme.BTN_3D` / `theme.BTN_3D_DANGER`** tokens (Tailwind arbitrary
`bg-[linear-gradient(…)]` + multi-layer `shadow-[…,…]` + `hover:`/`active:` variants, applied
with `color=None`); each page's `ui.add_css` block is slimmed to Quasar-table-internal rules
only (cell `td/th` padding, sticky `thead tr th`, `.q-table__middle` max-height, the scanner
`.q-tab`/`.q-tab__indicator`/`.q-tab--active` chrome). A `test_no_inline_style.py` guard
asserts all six files are `.style(`/`:style=`-free. Live-verified (computed styles): the
gradient/multi-layer-shadow buttons render exactly, tab accents + active underline, score/
heat/rec badge bg colors, cash/P&L text colors, and the 3:2 rescue column split — no console
errors. Next is Phase 3b. Prior — **Validated swing (1–8 wk) evaluation —
Trade page**:
the `/trade` **Position** verdict's hand-weighted swing scoring is **replaced by a
backtested, IC-weighted cross-sectional factor model** (investing/months deferred —
needs point-in-time fundamentals). A new PURE factor library
(`trade-analyzer/src/analysis/factors.py`: 10 causal, sign-corrected, daily-OHLCV
factors + a registry — `mom_12_1`/`mom_6_1`/`pth`/`str_5d`/`vol_adj_mom`/
`trend_quality`/`low_vol`/`rs_spy`/`rs_sector`/`turnover`; no look-ahead — winsorized
**cross-sectionally at scoring**, not per-factor) feeds an OFFLINE harness
(`trade-analyzer/backtest.py` IC/ICIR/quantile-spread/`zscore_by_date`/**signed
IC-weighting**/`walk_forward`/`calibrate` + the orchestrator `fit_swing_model.py`,
**run manually — NEVER in the request path**) that pulls ~78 liquid symbols' 5-yr
daily history, builds a (date,symbol) panel with **20-day forward EXCESS-return-vs-SPY**
labels, and writes the versioned **artifact `trade-analyzer/data/swing_model.json`**
(signed weights + per-factor IC + cross-sectional norm + score→outcome calibration +
walk-forward OOS IC) + a markdown research report (both gitignored under `data/`). A
LIVE scorer (`services/trade_svc/swing_model.py`, on-demand in `analyze()`, defensive →
falls back to legacy/None) z-scores the symbol's current factors on the artifact's
**cross-sectional norm** (calibration-consistent), **clips z to ±3**, composites with
the signed weights, and reads **BUY/SELL/HOLD off the calibration band** + a percentile
+ expected forward return + beat-SPY hit-rate. Additive optional **`swing_model`** block
on `TradeAnalysis`; a daily `cache:trade:universe_factors` snapshot is a defensive
fallback basis. The `/trade` Position card shows the validated verdict as the headline +
a calibrated outcome line + a **"Why — validated factors"** evidence expander, with the
**legacy heuristic** verdict tucked into a collapsed expander (Investor + Markov cards
unchanged — the **Markov card still forecasts the legacy technical-momentum score**, a
separate lens, a documented coexistence). **Validated result (current fit):** composite
**OOS IC ≈ +0.037** (5 of 13 walk-forward folds negative — the edge is thin +
regime-dependent); top quintile ≈ **+1.35% / 4 wk at 52% beat-SPY**, bottom ≈
**−0.80% / 43%**; signed weights low_vol **−0.34** (reclaimed with a NEGATIVE weight —
high-vol outperformed in this large-cap bull period), mom_12_1 **+0.21**, mom_6_1
**+0.17**, trend_quality **+0.12**, rs_sector **+0.08**, turnover **+0.07**. **Honest
caveats:** survivorship + non-stationarity; the edge leans on low_vol's inverted sign
reflecting this 5-yr bull-ish regime; **re-run `fit_swing_model.py` periodically**;
regime-conditional weighting (Option C) is the planned next step. See the **"Validated
swing evaluation (Trade page) — DONE (2026-06-28)"** section below + the manuals
(rebuilt). Design/plan:
[design](docs/plans/2026-06-22-swing-validated-evaluation-design.md) /
[plan](docs/plans/2026-06-22-swing-validated-evaluation.md). **Prior — 2026-06-27**
(**Gamma Analyze → live Claude API + infographic +
4×/day auto-run**: the `/options/gamma` **Analyze** button no longer copies a prompt
to a dialog — it now **calls Claude (Sonnet 4.6, thinking disabled, ~1.5k max-tokens)
via a forced `submit_analysis` tool-use call** and renders the structured reply as a
self-contained dark **infographic** served in a new browser tab (mirrors Explain's
`/options/<view>` raw-`HTMLResponse` route pattern): a **regime banner + bias meter**,
a **per-index card** ($SPX/SPY/QQQ) with a **price-level ladder** (spot vs gamma flip /
call+put walls / expected-move band, with label de-collision) + **metric tiles** +
note + a **per-symbol what-if** (▲ rally / ▼ sell-off / ▬ chop), and a **"Why is this
happening"** section at the bottom. The **Exp. move** tile is a **code-authoritative
1-day EM** (`spot·ATM_IV·√(1/365)` via `compute._session_expected_move`) that overrides
the model's copy — the engine's `calc_expected_move_from_chain` is a 0-DTE
remaining-hours EM that collapses to ~0 off-hours / at the close (the bug that surfaced
SPX EM ≈ 3). It also **auto-runs on a schedule** (`scheduler.analyze_slot_due`: premarket
09:00 ET / ~18 min after open 09:48 ET / midday 12:30 ET / close 15:58 ET, once per
trading day within a 20-min grace) → `handlers.run_scheduled_gamma_analyze` caches each
under its **own slot key** (`cache:options:gamma_analyze_{premarket,open,midday,close}`,
separate from the ad-hoc `cache:options:gamma_analyze` so a scheduled run never
auto-opens a tab); the Gamma page's **Auto briefings** buttons open each via
`/options/analyze?slot=…`. Every failure degrades to a readable HTML page (no chains /
no key / API error / no tool reply); output carries **no disclaimers**. Anthropic key
resolved locally in `compute` (env `ANTHROPIC_API_KEY` → gitignored
`shared/anthropic_key.txt`; options_svc does NOT import driver_svc). See the **"Gamma
Analyze — Claude infographic + auto-run (DONE 2026-06-27)"** section below. **Earlier
this session — EoD report redesign + Scanner/Paper/Driver UX batch**:
the **`/eod` report** was rebuilt around **Daily / Weekly(WTD) / MTD performance per
book** (manual ledger + Driver, separately) + **trade-type breakdowns** (strategy /
0-DTE-Swing / status) + **TOC + collapsible `<details>` nav** (no JS — works in-app and
in the exported files); needs the new additive `compute.driver_account_view()`
**`closed_positions`** field. See the "EOD Report redesign — DONE (2026-06-27)" section
below + [design](docs/plans/2026-06-27-eod-report-redesign-design.md) /
[plan](docs/plans/2026-06-27-eod-report-redesign-plan.md). **Also shipped this session**
(Scanner / Paper Trades / Driver UX batch + denser nav, commit `36bcf40`): Scanner
Calculator-transfer fix (legs were wiped pre-chain-load — stash `pending_legs` +
`load_symbol`) + tab counts/colors + in-app new-signal toast + 3D Run-scan button;
Paper Trades **live unrealized P&L** (reprice open ledger trades via `signal_repricer`,
market-hours gated) + colored/decimal P&L + renamed headers + newest-first sort +
**red Delete buttons** (needed `color=None` so `.pt-danger` beats `bg-primary`) +
descriptive **Analyze popup** + speedometer PoP-fallback + "Current price" label;
Driver **today-only decision log** + colored perf P&L + Bucket/Instrument labels +
sticky headers + the **root-cause fix that the driver never opened a position**
(`open_driver_position` KeyError'd on `'signal_id'` because the driver feeds RAW scanner
signals `type`/`credit`/`id`, not `strategy`/`entry_credit`/`signal_id` — see
[[driver-feeds-raw-scanner-signal-shape]]); nav inter-item spacing halved + all groups
expanded by default. Prior — 2026-06-25 (**Driver isolated paper account + performance
scorecard**: the autonomous Driver now trades into — and measures itself against —
its **own dedicated paper book** (`options-scanner/data/paper_account_driver.db`, new
`repo_paths.DRIVER_PAPER_DB`, $25k start), fully isolated from the user's manual paper
account. This fixes a latent **write/read split** found while investigating "where do
the driver's trades show up?": the Driver **wrote** `paper_create` into System A (the
flat LEDGER `trades.db` — no repricing/auto-manage/account, so its trades were inert
rows and the `source="driver"` tag was silently dropped) but **read** its day-P&L /
$500-target / halt from System B (the user's `paper_account.db` ENGINE account) — so it
measured the **wrong book** and its trades never repriced. Now a new
**`driver_paper_create`** command (`services/options_svc`) opens each guardrail
survivor into `DRIVER_PAPER_DB` via the new `compute.open_driver_position(signal, qty)`
(extracted from `paper_engine.run_entry_cycle`'s per-signal block — simulated fill →
re-size on the ACTUAL fill credit → reserve BP → `insert_position`; the guardrail qty
is a **CEILING**, `min(clamped, sized-on-fill)`; never raises), and the **5-min manage
tick** reprices + auto-exits the driver account on the existing `manage_due` slot in its
**OWN guarded branch** (`compute.run_driver_manage_cycle`) so a driver failure can't
skip the manual refresh. options_svc publishes two new views:
**`cache:options:driver_paper_account`** (snapshot + open positions — **NO rescue
overlay**, that reads the manual book) and **`cache:options:driver_paper_perf`** (a PURE
`driver_perf.build_scorecard`: # trades, open/closed, **win rate**, **profit factor**
[None when no losses yet], avg win/loss, realized/unrealized/total P&L, best/worst,
**P&L by symbol & by strategy**). `driver_svc` rewired: `run_autonomous_cycle` enqueues
`driver_paper_create` (not `paper_create`), reads day-P&L + positions from the **DRIVER**
account (`CACHE_OPT_DRIVER_PAPER`), and attaches the scorecard to the published
**`AutonomousState.perf`** (new additive field); `build_packet`'s open-position
attribution is correct-by-construction (the whole driver DB is the driver's; the dead
`source=="driver"` filter falls back to the full account). The `/driver` **monitor
re-points** its Day-P&L bar / summary / open positions to
`cache:options:driver_paper_account` (was the manual `paper_account`) and gains a
**Performance scorecard card** (headline + quality chips, best/worst, by-symbol /
by-strategy tables) reading `cache:options:driver_paper_perf` (live — refreshes on the
5-min tick, not just the 30-min cycle). **driver_svc must NOT import `paper_engine`**
(it transitively pulls `scoring`/`signal_repricer` → the documented cross-app module
collision) — all engine calls stay in options_svc; driver_svc only enqueues + reads
cache. **Also this session** (supporting fixes, shipped): a **`DRIVER_MODEL`** override
(env → gitignored `shared/driver_model.txt` → default `claude-opus-4-8`) so the decider
runs e.g. `claude-sonnet-4-6` per-deployment; decision-log timestamps in **CST**
(`to_central`); the Enable/Disable toggle hardened (optimistic state + timeout warning —
the real "switch keeps turning off" cause was driver_svc being DOWN, i.e. no consumer
for the enable command). New: `DRIVER_PAPER_DB`; `open_driver_position` /
`run_driver_manage_cycle` / `driver_account_view` / `driver_account_perf` /
`driver_perf.build_scorecard` (options_svc); `driver_paper_create` / `driver_paper_manage`
/ `driver_paper_reset` commands + the two cache views; `AutonomousState.perf`. **PAPER
ONLY** — `config.PAPER_TRADE` stays True; the driver never flips it. options_svc **285**
+ driver_svc **138** + contracts **35** + webgui **510** green (incl. a Redis-driven e2e
proving a `driver_paper_create` lands ONLY in the driver DB — manual account untouched —
and both views + the scorecard reflect it). Built subagent-by-subagent (TDD, two-stage
spec+quality review per unit). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-25-driver-isolated-paper-account-design.md) /
[plan](docs/plans/2026-06-25-driver-isolated-paper-account-plan.md).)
Prior — 2026-06-24 (**Autonomous Driver — strategy-agnostic Claude
decision layer (level B, paper)**: the `/driver` morning agent's hardcoded
`trade_selector` rule tree (three fixed buckets — the reason only equity trades
ever appeared: the SPX-options + MES-futures branches hit hardcoded gates and the
tree fell through to the one branch that always passed) is **replaced by a
Claude-driven decision layer** that auto-selects + sizes **defined-risk option
credit spreads (PCS/CCS/IC) from the scanner menu** (`cache:options:scan`),
targeting **net $500/day**, in **autonomous paper** mode (NO human approval gate;
`config.PAPER_TRADE` stays True — this service never flips it). Pipeline (all new
in `services/driver_svc`): **`compute.build_packet`** projects the top-N
composite-scored scanner signals into a compact model-facing menu + day-P&L
gap-to-target, keeping a `menu_by_id`→RAW-signal map (real scanner field names:
structure in **`type`**, **`expiration`**, **`pop_pct`** — the plan's guesses were
wrong, caught during the build) → **`decider.decide`** (Claude **Opus 4.8** via the
new `anthropic` dep; a forced `submit_decision` tool-use call; ANY failure — no key
/ API error / malformed output — degrades to **stand-down**, never trades blind;
`import anthropic` is lazy) → **`guardrails.apply_guardrails`** (the **PURE
code-authoritative safety core**: a defined-risk allowlist, a per-trade + daily-
budget **quantity clamp**, and a **halt** at banked-$500 / daily-loss-cap / VIX>25
— the model PROPOSES, code DECIDES; the model never sizes its own risk; hardened
vs NaN/inf) → enqueue the EXISTING `cmd:options` **`paper_create`** per survivor (a
`source="driver"` COPY of the signal + the CLAMPED qty; the enqueue loop is
isolated so a mid-loop failure can't skip the halt-latch/publish). A
**`cache:driver:control`** key is the **master switch + STOP kill-switch** (default
**OFF** — the user explicitly enables); the scheduler fires a cycle at **09:28 +
every 30 min during RTH** (`checkpoint_due`) with a **next-day halt re-arm**
(`should_rearm`), each on the executor + per-branch guarded. The `/driver` page
flips to a **monitor + override** — Enable/Disable, a confirm-gated **STOP**, **Run
now**, a day-P&L-vs-$500 progress bar, open-driver-positions, and a newest-first
**decision-log** audit — reading **`cache:driver:autonomous`** (`AutonomousState`);
the **legacy approval queue + Performance** UI is preserved (gated off while
autonomy is enabled). The decider only PICKS from the scored scanner menu (never
invents strikes), and the legacy `trade_selector` is retained as a degrade path.
**Real `/ES` `/MES` futures options (FOP) were investigated and shelved** — Schwab's
API serves no FOP chains and places only EQUITY/OPTION orders (see
[[schwab-api-instrument-limits]] / the design doc); SPX/NDX index options are the
cash-settled 1256 equivalent if revisited. New contracts `DriverControl` /
`AutonomousState`; new `anthropic` dep + `ANTHROPIC_API_KEY` (env / gitignored
`shared/anthropic_key.txt`). driver_svc **130** + shared/contracts **34** + webgui
**483** green (incl. a Redis-driven e2e proving the model's requested qty=3 **clamps
to 1** through the REAL pipeline, and a banked-$600 cycle latches the kill-switch).
Built subagent-by-subagent (TDD, two-stage spec+quality review per unit). Branch
`Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-driver-autonomous-claude-decider-design.md) /
[plan](docs/plans/2026-06-24-driver-autonomous-claude-decider-plan.md).)
Prior — 2026-06-24 (**Calculator/Simulator UX batch — symbol tab/Enter
Load, wait overlay, Expiry→all legs, compact leg cells**: four Tier-1 UI changes to
`/options/calculator` + `/options/simulator` (no service/contract changes).
**(1)** The **Symbol** field now fires **Load** (Calculator) / **Fetch snapshot**
(Simulator) on **tab-out (`focusout`) + Enter (`keydown.enter`)** — `focusout` not
`blur` (NiceGUI binds the q-input ROOT where `blur` doesn't bubble) — deduped via the
new PURE `inputs.should_load(current, last_loaded)` so an unchanged symbol doesn't
re-fetch; the **Load/Fetch BUTTON still force-reloads** (bypasses the dedup), and a
`state["loading"]` re-entrancy guard collapses the focusout-then-button-click double
fire. **(2)** A **centered full-screen wait overlay** — new shared
**`pages/options/overlay.py`** `build_loading_overlay()` → a handle with
`.show(msg)`/`.hide()` (a `position:fixed` dimmed backdrop + `ui.spinner`, built once
per render) — shows on **user-initiated** loads (`show_wait=True`), hides on
chain/meta arrival (`_apply_chain`/`_apply_meta`), with a **safety timeout**
(`overlay.LOAD_TIMEOUT_SEC=30s`, shared) that also resets the dedup. The timeout was
**raised 15s→30s after live-measuring the Simulator's `sim_fetch` at ~19s for SPY**
(6870 contracts) — 15s fired before a real snapshot landed, hiding the spinner
prematurely; the overlay's PRIMARY dismissal is data-arrival, so the backstop must
exceed the slowest legitimate fetch. Mount-time auto-loads (persisted-state restore /
cross-page handoff) pass `show_wait=False` (no overlay flash on every navigation).
**(3)** The Calculator's **top-level Expiry propagates to ALL legs** (literal, incl.
calendars — the user's choice) via `leg_editor.apply_expiry` / PURE
`set_legs_expiry(legs, expiry)`, which re-syncs each leg's strike select to the new
expiry; wired on `expiry_sel.on_value_change` → `_on_expiry_change`, **guarded by
`state["applying"]`** so the programmatic expiry sets in `_apply_chain`/`_prefill`
don't fire it, and the editor **`dirty` flag is preserved** so an untouched
single-expiry template still routes through the analytic summary. The **Simulator has
no global expiry** (per-leg only), so this is Calculator-only. **(4)** The shared
**`leg_editor`** leg-table cells are **compact** (a `leg-row` class on each row +
`theme.py` `.leg-row` CSS: `min-height:32px` + trimmed top/bottom AND side padding)
and the **Type** column widened (`w-20`→`w-24`) so **`call`/`put` no longer clip**
(verified: "call" renders 20px in a 58px cell), and the **"Actions" header is
dropped** (an empty `w-10` spacer keeps the trashcan column aligned) — **both pages**
(shared editor). New PURE helpers (`should_load`, `set_legs_expiry`) + the overlay
handle are unit-tested; webgui **460 green**; **verified live** (Calculator: AAPL
tab-out + MSFT Enter load with overlay show/dismiss; Simulator: SPY tab-out → overlay
→ ~19s snapshot → legs populate at 732/731 near spot 733.24, status "SPY spot 733.24 ·
6870 contracts"; compact cells + full "call"/"put" + no "Actions" header on both).
Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-calculator-simulator-ux-changes-design.md) /
[plan](docs/plans/2026-06-24-calculator-simulator-ux-changes-plan.md).)
Prior — 2026-06-24 (**Gamma page overhaul — persistence, fixed strike
window, blended heatmaps**: a batch of `/options/gamma` fixes. **(1) Symbol no
longer reverts to `$SPX` on refresh** — the page reads one shared
`cache:options:gamma`, so the **dropdown syncs to the cached snapshot's symbol on
build**, a repaint **ignores any snapshot whose symbol ≠ the selected one** (so the
service's one-shot `$SPX` startup publish can't clobber it), and **selecting a symbol
auto-refreshes** it (`gamma._set_symbol`/`_on_symbol_change` + the `_maybe_repaint`
guard). **(2) Fixed ±N strike window** (`gamma.strikes_around`, N_SIDE=20) for the
bars **and** heatmap instead of a ±% band, so the candle/cell count — hence size —
stays consistent through the day; the heatmap `rowsize` is the **median** visible-
strike gap (not the min) so mixed-spacing names (QCOM/SPCX: 1.0 strikes among 2.5)
tile densely like `$SPX` (`_strike_step`). **(3) Heatmap cropped to the visible
near-spot window** before building cells — Charm/DEX/Vanna are non-zero across the
whole chain, so this cut ~45k→~2.4k points (~19×). **(4) Off-hours persistence** —
the candles + heatmap stay on the **last session's** data until the **next trading
day's midnight CT**, then clear (Fri persists through the weekend / holidays until
the pre-session midnight): `scheduler.active_session_date()`/`gamma_cleared()` +
`gex_history_db.load_date_with_grid(date)`; `compute.gamma_snapshot` returns empty
in the overnight cleared window and loads the **active session date** for the
heatmap (service-side; the DB retains prior rows). **(5) Blended heatmaps** — both
the intraday **and Term** heatmaps render as a smooth **interpolated** image (no
cell borders / separator mesh), a **dark diverging colorscale** (`HEAT_STOPS`: net
≈ 0 fades to **transparent** so the dark page shows through, like the candlestick
chart; strong −/+ glow red/green), a **transparent** chart background, an
**off-white** (`#f5f5f5`) spot line, **no fade** on hover (`states.inactive`/`hover`
disabled), and a **press-and-hold tooltip** — a `chart.events.load` hook
(`_HEAT_PRESS_TOOLTIP_JS`) gates Highcharts' `tooltip.refresh` so the popup shows
**only while the left button is held** (mousedown → show + follow the cursor;
mouseup → hide); plain hover shows nothing. **(6) Term view bugfixes** — re-floats
JSON-stringified strike keys + widens the chain fetch to the **next 5 expirations
regardless of cadence** (`compute._term_chain`/`_count_expirations`, so weekly/
monthly-only names show 5 columns, not 1). **(7)** an off-hours `spot=None` snapshot
no longer 500s the page. webgui 455 + options_svc 256 green; verified live. Branch
`Using_Highcharts`.)
Prior — 2026-06-24 (**Calc "Number of strikes" + Calc/Sim state persistence**:
two changes. **(1)** The Calculator's **Range min/max/%** controls are replaced by a single
**Number of strikes** input (default 24): the P&L grid now draws **±N real chain strikes
around spot** (strictly — a far-OTM leg can fall off; raise N to see it). New pure
`calculator.strikes_window(strikes, spot, n)` (the n strikes ≤spot + n >spot from the
front-expiry call∪put ladder) feeds an explicit **`price_rows`** list into
`compute.calc_compute` → engine `calc_spread_pnl(price_rows=…)` (additive — used verbatim
as the grid rows, else the even-step ±N heuristic fallback). `calc_compute`'s
`range_min/max/pct` params + `symmetric_price_range` are **removed**; the `calc_compute`
handler is `**args`-generic so it needed no change. **(2)** Both `/options/calculator` and
`/options/simulator` now **persist full UI state across navigation** and **auto-refresh on
return** — a single-user module-level snapshot (`_LAST_CALC`/`_LAST_SIM`) captures every
input (symbol/strategy/legs/fields/sliders[/active tab]) on change and restores it on
`render()` under a `restoring` guard (so wiring fires no stray commands); restored legs
ride each page's existing `pending_legs` hook so the post-fetch re-run uses them (an
explicit **Copy-to-Calculator/Simulator handoff still wins** over the snapshot — see
`page_state.pick_seed`). Survives navigation + browser reload; resets on a webgui restart
(like every persisting page). New PURE `webgui/pages/options/page_state.py`
(`snapshot`/`merge_restore`/`pick_seed`). options-scanner 17 + options_svc 249 + webgui
450 green; verified live (Number-of-strikes: AAPL grid = 24 rows = ±12 real strikes
265→322.5; persistence: AAPL+12 / MSFT restored across nav + price auto-refreshed;
service contract via Redis: N=5 → exact ±5 strikes). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-calculator-simulator-state-persistence-design.md) /
[plan](docs/plans/2026-06-24-calculator-simulator-state-persistence.md).)
Prior — 2026-06-24 (**Simulator What-if P/L fix**: the `/options/simulator`
**What-if** payoff had two bugs vs the Calculator for the *same* trade. (1) **Missing
×100 contract multiplier** — the simulator engine prices in **per-share × qty** units,
so a 10-lot spread's curve read **100× too small** (a real ~$14.5k max loss showed as
**−$200**). `compute.sim_run` now scales each `whatif_rows` `theo_price` by
`_CONTRACT_MULT=100` → a **dollar** position value (the Calculator scales by the same
literal 100). (2) **Wrong P/L baseline** — `whatif_pnl` subtracted the position's value
at spot at the **forward** time ("zero at spot"), so the profit side capped at **0** and
the whole curve was off by the credit. It now measures **from entry**: `sim_run` returns
**`whatif_baseline`** = the position's $ value at **spot, NOW** (the entry mark, Δt=0
full DTE), and `whatif_pnl(df, spot, baseline)` / `whatif_figure(..., baseline)` plot
`value(S,t) − baseline` — identical to the Calculator's `entry_credit + value(S,t)`
([options_calculator.py](options-scanner/options_calculator.py) `val += price*q*100;
pnl=entry_credit+val`), so **profit caps at the net credit, loss floors at width−credit**,
and theta now shows as the **Δt** slider moves (the old framing pinned spot to 0,
hiding it). No-baseline `whatif_pnl` keeps the legacy nearest-spot fallback (back-compat
/ pre-restart cached results); the IV-shock + Replay tabs are unchanged. Verified on the
**real** engine (not just the fakes): a 20-wide 10-lot SNDK call credit spread yields
`|max-profit| + |max-loss| = $20,000` (= width×100×qty) with profit=credit and
loss=−(20000−credit). webgui 438 + options_svc 253 green. **Restart `options_svc` +
reload the page** to see it live (the running service/page are stale). Branch
`Using_Highcharts`.)
Prior — 2026-06-24 (**Trade Analyzer theme + Markov near-term fix**: the
`/trade` page now wears the shared dark-navy **"dashboard" theme** (`ui.add_css(
DASHBOARD_CSS)` + `.calc-v2` wrap from `webgui/pages/options/theme.py`; header +
verdict + secondary cards are `calc-card`s, the Analyze button is `cv2-btn-primary`).
**Dead space removed**: the verdict row switched `items-stretch` → **`items-start`**
so the short Position/Investor cards size to content instead of stretching to the tall
Markov card (verified live: 308/276px vs the Markov card's 453px — was ~150-180px of
empty bottom each). **Markov chart fix** for "looks the same for every symbol": the
5/10/20d forecast **converges to the bull-leaning pooled-prior stationary within
~10 days** (only the near term is score-specific), so `trade_svc.compute.build_markov_block`
now emits an **additive** dense **`trajectory`** (`_MK_TRAJECTORY_HORIZONS=[1,2,3,5,10,20]`,
reusing the tested `forecast()`) and `trade.markov_forecast_figure` plots it
(`now→1d→2d→3d→5d→10d→20d`, falling back to `horizons` for back-compat) — the
score-specific early path is now visible (verified live: XOM Strong-Bear opens
red-dominated, INTC Strong-Bull green-dominated, NVDA between). The chart is
dark-themed (transparent bg, light axes, fixed `{value}%` y-axis). **`horizons`
(5/10/20d cards), `drift`, `tilt`, `markov_adjusted_score` are unchanged** — the
trajectory is chart-only, the verdict label/score math untouched. **Tab-out =
Analyze**: a **`focusout`** handler (NOT `blur` — NiceGUI binds to the q-input ROOT
and `blur` doesn't bubble there, same reason `select_all_on_focus` uses `focusin`)
fires Analyze, deduped via `should_request` (collapses the blur-then-click double
fire). **Last analyzed symbol persists** across navigation: the input seeds from the
cached `trade:analysis` result's `symbol`. webgui 435 + trade_svc 41 green; verified
live end-to-end (themed render, dense chart, tab-out analyze, symbol persistence).
Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-24-trade-analyzer-theme-layout-markov-design.md) /
[plan](docs/plans/2026-06-24-trade-analyzer-theme-layout-markov-plan.md).)
Prior — 2026-06-24 (**App theme rollout + What-if payoff**: the dark-navy
**"dashboard" theme** is now **shared, not Calculator-only** — extracted to
**`webgui/pages/options/theme.py`** (`DASHBOARD_CSS`, scoped under `.calc-v2`) and
injected by **both** the Calculator **and** the Simulator (`ui.add_css(DASHBOARD_CSS)`
+ wrap in `.calc-v2`), so the look never drifts. The **Simulator** gains the navy
gradient, bordered `calc-card`s, the **boxed** Strategy picker, **header-table** legs,
`cv2-btn`/`cv2-btn-primary` buttons, and **dark transparent Quasar tabs** so its
already-dark-transparent Highcharts panels sit on the navy. See the new **"App theme
— dark-navy 'dashboard'"** reference section below (palette + class vocabulary +
how-to-apply) — the single place to look up the theme. The Simulator **What-if** tab
is restyled as a **green/red profit-loss payoff**: `simulator.whatif_figure` plots
`P/L = theo_price(S) − theo_price(spot)` (`whatif_pnl`, **zero at spot**) as a
Highcharts **area** with `threshold:0` + `color`/`negativeColor` + `fillColor`/
`negativeFillColor` (green profit fill+line above the breakeven, red loss below; an
explicit base `color` stops a default-blue base path leaking under the split) + faint
full-height Profit/Loss `plotBands` with labels; `theo_price` is already
sign-weighted by leg side (`aggregate_position` scale `sign*ratio`), so the
subtraction is the holder's P/L and the direction is correct (verified live: a
24-DTE SPY bull put spread loses on the downside, profits on the upside). webgui 430
green. Branch `Using_Highcharts`.)
Prior — 2026-06-23 (**Multi-leg Simulator + Calculator DONE**: the
`/options/simulator` and `/options/calculator` pages now build, price, and analyze
**multi-leg strategies** — verticals (credit *and* debit), condors (iron +
all-same), butterflies (long 1-2-1 + iron), and **calendars/diagonals** (per-leg
expiry) — with **editable legs** and a **copy-legs button both ways**
(Simulator ↔ Calculator); existing singles + PCS/CCS/IC stay. New shared **pure**
`webgui/pages/options/strategies.py` (normalized leg dict + `STRATEGY_TEMPLATES`/
`STRATEGY_GROUPS` + `build_default_legs` + analytic-vs-numeric `summary_code`) and
`webgui/pages/options/leg_editor.py` (one parameterized editable leg-table widget
both pages mount — `state['legs']` is the source of truth so re-renders never lose
edits; each page injects its own `strikes_for`/`expiries_for` + `show_premium`).
Engine `options_simulator/engine.py` gains `Leg.ratio` (+ `Position.from_legs`) so
`aggregate_position` scales each leg by `sign*ratio` (butterfly body = 2×). Calc
engine: `calc_spread_pnl(per_leg_expiry=True)` prices each leg at its own
time-to-expiry (calendars) + new `calc_summary_generic` (numeric max-P/L /
breakevens / PoP off the value-at-front-expiry curve) for butterfly/condor/
calendar/`CUSTOM`; `compute.calc_compute` routes analytic (PCS/CCS/IC/singles) vs
generic and runs the grid to the **front (nearest) leg expiry**. `compute.sim_run`
(per-leg **elapsed** What-if Δt — a deliberate change from absolute-DTE, fixes
calendars) + `compute.sim_replay` are now **multi-leg** (back-compat with the old
single-contract args); `handlers` forward a `legs` arg; `handoff.py` adds the
`simulator`/`calculator_legs` stashes + `send_to_simulator`/`send_to_calculator_legs`.
options-scanner engine+calc + options_svc 252 + webgui 419 green; verified live
(SPY calendar `sim_run`/`sim_replay` 234-bar trace + an iron butterfly with exact
`max_loss=2300`/`breakevens=[727,741]`). Branch `Using_Highcharts`. Design/plan:
[design](docs/plans/2026-06-23-simulator-calculator-multileg-strategies-design.md) /
[plan](docs/plans/2026-06-23-simulator-calculator-multileg-strategies-plan.md).)
Prior — 2026-06-22 (**Markov 2.0 (Trade Analyzer) DONE**: the `/trade`
**Position** verdict gains a probabilistic forward layer — the composite score is
discretized into **5 bands** (edges = the ±40 BUY/SELL cuts + ±15 neutral); a
per-symbol day-to-day transition matrix is **Bayesian-shrunk** toward a pooled
(17-symbol) prior; `P^n` projects **5/10/20-day** band distributions →
P(BUY)/P(SELL)/E[score]. New PURE engine `trade-analyzer/src/analysis/markov.py`
(classify/count/shrink/project/forecast/`drift_tilt`); `trade_svc.compute`
`reconstruct_daily_composite` (a daily-only **"Markov base score"** so history is
reconstructable) + `build_pooled_prior`/`get_prior` (cached
`cache:trade:markov_prior`, lazy daily) + `build_markov_block` wired **defensively**
into `analyze()`; an additive optional `markov` block on the `TradeAnalysis`
contract; a **Markov Forecast card** — the third **equal-width frame in the verdict row**
alongside Position/Investor — (stacked-area band-probability chart + per-horizon
metrics + a bounded **±12pt confidence-weighted drift tilt** surfaced as a
`markov_adjusted_score` Position headline — **verdict label unchanged**). No
feedback by construction (chain on `composite_daily`, tilt on `composite_full`).
trade-analyzer 215 + trade_svc 40 + contracts 26 + webgui 385 green; verified live
(AAPL). Branch `Using_Highcharts`. See "Markov 2.0 (Trade page)" below.)
Prior — 2026-06-21 (**Rescue Tested Trades DONE**: new `/options/rescue`
page + an advisory/one-click-apply rescue feature for tested credit spreads (PCS/CCS/
IC). Hybrid arch ("Approach C"): cheap at-risk detection rides the existing 5-min
manage cycle (tags paper-account rows with `rescue_state`/`heat` + publishes
`cache:options:rescue_summary` for a nav badge); the ranked candidate menu is computed
on-demand via a `rescue` command → `cache:options:rescue:<position_id>`; apply executes
via new paper-engine primitives behind a stale-price guard. New PURE engine
`services/options_svc/rescue.py` (11 candidate builders + risk/context/scoring),
`compute.compute_rescue`, `handlers.rescue`/`rescue_apply` + summary overlay,
`options-scanner/paper_adjust.py` (apply primitives + dispatcher), `paper_account_db`
`position_adjustments` table + `parent_position_id` col, `config/commissions.toml`
(commission source of truth), `RescueAdvisory`/`RescueCandidate` contracts.
shared/contracts 24 + options_svc 226 + webgui 372 green; options-scanner 1056 (12
pre-existing fails). Verified live (real INTC paper positions). Branch
`Using_Highcharts`. See "Rescue tested trades" below.)
Prior — 2026-06-20 (**Replay + Expected Move look-back, DTE-aware**: the
Simulator Replay path and the Expected Move trailing history now size to the
selected contract's **DTE** (Replay tiers 1-min/1d → daily/~½×DTE; EM ≈ **3× DTE**)
with an **auto + manual-override** look-back dropdown on each tab. EM also
**collapses non-trading-day gaps** — renders via `ui.highchart(type="stockChart")`
→ ordinal x-axis + a trading-day-only `em_cone` (reuses `scheduler._HOLIDAYS`) — and
the Replay hover tooltip is capped at 2dp. 356 webgui + 145 options_svc tests green;
verified live. Branch `Using_Highcharts`. See "Simulator Replay tab" + "Expected
Move page".)
Prior — 2026-06-20 (**Simulator Replay tab migrated**: the third legacy
Tk simulator tab (`Replay`) now lives in the 3-tier webgui alongside What-if /
IV-shock — new `compute.sim_replay` + `sim_replay` command +
`cache:options:sim_replay` + a stacked price+5-Greek Highcharts panel with a
client-side scrub cursor. Verified live (SPY 62-bar trace). See "Simulator Replay
tab" below.)
Prior — 2026-06-19 (**charting migrated Plotly/SVG → Highcharts**: every
webgui chart + gauge now renders via `nicegui-highcharts` (`ui.highchart`), not
`ui.plotly`/inline-SVG. Pure builders return Highcharts option dicts; in-place updates
are `el.options = fig; el.update()` (replaces `update_figure`). The Sentiment / Trend /
Trade-detail speedometers are the shared **`webgui/pages/gauge.py`** angular gauge
(painted red→yellow→green rainbow face + needle). Key gotchas now in the "NiceGUI
gotchas" section: the `gauge` type needs NO `extras` (auto-loads via `loadMore`;
`extras=["highcharts-more"]` throws), `solid-gauge`/`heatmap` ARE valid extras, a
dynamically-added chart needs a chart already present at first render (ESM import map),
`bar` axis is reversed by default, and `chart.update()` leaks config across a
series-type switch (recreate on kind-change). `plotly` was never a Python dependency.
322 webgui tests green; verified live. Branch `Using_Highcharts`.
Prior — 2026-06-19 (**intraday Market Trend redesign**: the Sentiment tab's
Market Trend gauge is now a responsive **directional 0–100 score** recomputed every
15 min — Price/MTF 45% + Breadth 25% + Sector 20% + VIX 10%, confidence-weighted,
EMA-smoothed needle, 5-state mapped (range widened to 30–70) onto the bridge with
2-read hysteresis so `regime_filter` is unchanged. New pure
`sentiment-dashboard/scoring/intraday_trend.py` + `services/sentiment_svc/compute.py`
`compute_intraday_trend`/`compute_30d_trend` + 15-min gated/persisted refresh +
additive bridge fields (`trend_score`/`sub_scores`, daily `sma_*` kept). Second gauge
is now the **30-Day structural** trend. See "Intraday Market Trend model" below.
Prior — 2026-06-19 (**perf-fix batch 2**: implemented the remaining High +
all Medium audit items — webgui health-cache + off-thread/de-duped alert watcher +
in-memory `app_settings` cache, Gamma's four polls coalesced into one cheap pipelined
`read_versions`, `Bus` cheap `:ver` version reads + `consume_commands` group-create
once, `technical` EMA/MACD/volume-profile vectorized, `sectors_ref` mtime-cache,
trader-path pooled session, sargable `gex_history_db` today-query. All suites green
(2 pre-existing sentiment-dashboard UI-import fails aside). Prior same-day: **perf-fix
batch 1** + **end-to-end audit pass**: corrected doc drift —
full `config/ports.toml` (memurai + ml_servers + services 8210–8214), the
`pages/options/handoff.py` + `pages/ui_guard.py` shared helpers, the Sentiment
3-column / 2×2-tile layout, the proxy `/pricehistory` 404-flood fix, and paper
auto-manage (no longer "manual-only"); added a **"Performance characteristics &
known hotspots"** section from an efficiency audit. Prior same-day: **System
Status page DONE**: new `/status` pure-webgui
page probes every tier — Memurai PING, schwab-proxy `/health`, the five domain
services' `/health` (:8210–8214), and webgui itself — into an overall up/down
banner + per-component cards, plus a **published-data-freshness** table (each
domain's latest cache version + age, flagging scheduled views gone stale). 304
webgui tests green. See "System Status page (`/status`) — DONE" below.
Prior — 2026-06-18 (**EOD Report page DONE**: new `/eod` + `/eod/detail`
pure-webgui pages aggregate the collected `options:*` + `driver:*` caches into a
**Summary** rollup + **Detailed** report, with a **Generate** button that snapshots
the caches into standalone `summary.html`/`detail.html` archived under
`webgui/data/eod/<date>/` (in-app view + dated archive + `/eod/file` raw serving).
See "EOD Report page (`/eod` + `/eod/detail`) — DONE" below.
Prior: **3-tier migration — all five domains migrated** (Sentiment, Options,
Portfolio, Trade, Driver) — every page reads Redis and the webgui imports only
`nicegui` + `shared.bus` + `shared.contracts`. Remaining: Phase 6 retire-shims
(`regime_filter` reads Redis; drop the bridge dual-write).)

## What this project is

A **self-contained NiceGUI web GUI** for the Schwab trading stack. It is a fork
of the active backend of the original `D:\Trading With Schwab` monorepo, with the
old per-app UIs (Dash, built React, Tk desktop) **replaced by a single NiceGUI
multi-page web app** (`webgui/`).

The original repo (`D:\Trading With Schwab`) is **reference only** — this project
does not import from it or depend on it at runtime.

## Tech stack

| Layer            | Technology                                                        |
|------------------|-------------------------------------------------------------------|
| Web GUI          | **NiceGUI** (`>=2.0`) — single multi-page app, Python-only        |
| Charts / gauges  | **Highcharts** via `nicegui[highcharts]` (`ui.highchart`) — all webgui charts + gauges |
| API gateway      | FastAPI + uvicorn (`schwab-proxy`)                                |
| Brokerage SDK    | `schwab-py` (`schwab` package) — auth, market data, streaming     |
| Data / numerics  | pandas, numpy, scipy                                              |
| Scheduling       | APScheduler (claude-driver)                                       |
| Notifications    | winotify (Windows toast)                                          |
| Spreadsheet I/O  | openpyxl                                                          |
| Testing          | pytest                                                            |
| Runtime          | Python 3.11+, Windows-first, single-user                         |

## Architecture

```
schwab-proxy (:8100)  ──HTTP──>  webgui NiceGUI app (:8500)
        │                              │
   owns Schwab auth/                   ├─ Options  page  → options-scanner engines
   tokens + market data               ├─ Sentiment page → sentiment-dashboard scoring
                                       ├─ Trade    page  → trade-analyzer src/analysis
                                       ├─ Portfolio page → portfolio-analyzer src (live)
                                       └─ Driver   page  → claude-driver orchestration
        │
   shared/analysis_lib  ← shared library (schwab_client, market_data, technical, mtf…)
```

**The proxy must be running first.** All feature backends resolve their Schwab
client and market data through `http://127.0.0.1:8100`.

## Planned 3-tier architecture (approved 2026-06-15 — migrating)

The monorepo is being re-tiered (strangler-fig) into three **physically separate**
tiers over a **Redis (Memurai) backbone**. Until a domain is migrated it keeps the
in-process model above; check the design doc for current step status. Target shape:

```
TIER 1 GUI         webgui/ NiceGUI (:8500) — render() only; reads Redis cache on
                   page build, subscribes to pub/sub for repaints, enqueues commands.
                   No engine imports, no Schwab calls, no sys.path glue.
        ▲ cache read / subscribe          │ commands
TIER 3 STORE+COMM  Memurai (:6379): cache:{domain}:{view} (replaces _CACHE/_LAST_RESULTS),
                   events:{domain}:{view} pub/sub (replaces bridge file + version polling),
                   cmd:{domain} Redis Streams (GUI→service RPC). shared/contracts/ (typed
                   payloads = the API) + shared/bus/ (redis-py wrapper, fakeredis under pytest).
                   On-disk DBs unchanged. sentiment_bridge.json kept as dual-write shim.
        ▲ publish                          │ consume
TIER 2 PROCESSING  services/{domain}_svc FastAPI (options/sentiment/trade/portfolio/driver):
                   each imports ONLY its engines, owns its scheduler/auto-scan + command
                   consumer, validates+caches+publishes. Separate processes ⇒ the scoring/
                   notifier sys.path collision class CANNOT occur (options_scoring() guard is
                   DELETED, not ported). Calls schwab-proxy (:8100) for data.
```

**Migration order:** Sentiment (reference) → Options → Portfolio → Trade → Driver →
retire shims (regime_filter reads Redis; drop bridge file). New ports to add:
`memurai=6379` + one per service (~8210–8214). `start_all` order: Memurai → proxy →
services → webgui. Full design: [3-tier design doc](docs/plans/2026-06-15-three-tier-architecture-design.md).

## Folder map (what was copied in)

| Folder                 | Role                                                        | UI status        |
|------------------------|------------------------------------------------------------|------------------|
| `schwab-proxy/`        | Central Schwab API gateway / token manager. **Start FIRST.**| backend, :8100   |
| `options-scanner/`     | GEX/options scanner engines, scoring, paper engine, simulator. | engines only (Dash UI dropped) |
| `sentiment-dashboard/` | Market sentiment `scoring/` + `history_backfill` + `live_composite.py` (live intraday composite + bridge payload) + `publish_bridge.py` (headless bridge writer) + bridge + `sectors_ref.py`. | ported to NiceGUI `/sentiment` |
| `trade-analyzer/`      | `src/analysis` — fundamentals, recommendation, scoring, sector. | engines only (Tk UI dropped) |
| `portfolio-analyzer/`  | `src/` — sector breakdown, vs-sector perf, live streaming.  | engines only (Tk UI dropped) |
| `claude-driver/`       | Morning/intraday orchestration + order approval logic.      | engines only (approval UI to be NiceGUI) |
| `shared/`              | `analysis_lib/` shared library + secret templates/values.   | library          |
| `tools/`               | `check_env.py`, `db_admin.py` maintenance utilities.        | CLI              |
| `webgui/`              | **NEW** NiceGUI multi-page front-end. Shell + Options section built. | the new UI, :8500 |

> The old UI entrypoints (`dashboard.py`, `sentiment_dashboard.py`,
> `trade_analyzer.py`, `portfolio_analyzer.py`, the React `frontend/dist`) were
> **not copied**. When porting a feature to NiceGUI, read those from the source
> repo `D:\Trading With Schwab` for reference.

## webgui structure (NiceGUI app)

`webgui/main.py` is the server + nav shell: a left-nav with expandable
**Options**, **Sentiment**, and **More** groups (Sentiment children: Sentiment
dashboard + Sector Rotation; **More** children: EOD Report + System Status +
Settings + Terminate) plus flat Trade / Portfolio / Driver items. The groups
**start EXPANDED by default** (`_NAV_OPEN.get(..., True)`) and stay open until the
user manually collapses one (`_NAV_OPEN` persists each toggle, single-user); the
**inter-item spacing is tight** (`_NAV_CSS`: flex gap 2px, link padding 4px,
expansion-header min-height 24px, and the **`.nicegui-expansion-content` gap is
overridden to 2px** — NiceGUI defaults that wrapper to a 1rem column gap, which is
the real source of group-child spacing, NOT `.q-expansion-item__content`). Pages
live in `webgui/pages/`; each leaf exposes `render()` called inside the shell
`_layout`. `webgui/proxy.py` wraps `schwab-proxy/proxy_client.py` and adds
`health()`. Pure transforms / SVG builders are unit-tested (`webgui/tests/`);
heavy engine calls run off-thread via `nicegui.run.io_bound`.

**App-wide alerts + nav badges (DONE — 2026-06-17).** `_layout` mounts a hidden
`<audio>` + a `ui.timer(2s)` watcher (`alerts.py` pure helpers + `main._run_watcher`)
that runs on **every** page: it chimes a bundled WAV (`webgui/static/sounds/{chime,
bell,ping}.wav`, served at `/static`) — and optionally fires a desktop
`Notification` — on new qualifying scanner signals (gated by enable/market-hours/
min-score in `app_settings`), and maintains red count badges on **Scanner** (new
signal keys), **Captured Signals**, and **Driver** (pending approval) nav items
(`_NAV_BADGES`, single-user like `_NAV_OPEN`; cleared when you open that page).
GUI prefs persist via `webgui/app_settings.py` → `webgui/data/settings.json`
(gitignored; regenerates from `DEFAULTS`). The **Settings** page (`/settings`,
`pages/settings.py`) binds the alert toggles/sound/volume/market-hours/min-score +
desktop-notification controls. The drawer is restyled (`.nav-drawer` CSS: active
pill, hover, right-aligned badges, title block). Browsers block autoplay until a
user gesture — clicking any nav link or **Test sound** unlocks it. Design/plan:
[design](docs/plans/2026-06-17-scanner-alerts-settings-badges-design.md) /
[plan](docs/plans/2026-06-17-scanner-alerts-settings-badges-plan.md).

Routes:

| Route | Page | Status |
|-------|------|--------|
| `/` | Options · Scanner (0-4 / 5-15 DTE, two-pane + detail panel; **distinct tab colors** — 0-DTE amber / Swing blue, each with a colored active underline — + **live signal counts in the tab headers** (`tab_label`); a new qualifying signal pops an **in-app toast** (`fiber_new`, blue-8 — matching the row "new" badge) alongside the chime/desktop notification; **Run scan** is the app's solid 3D button (`color=None` + `.scan-btn`); the per-row **Send to Calculator** now transfers correctly — `_prefill` stashes `pending_legs` + `load_symbol()` so legs apply AFTER the chain loads, instead of being wiped by strike-coercion against an empty chain (see [[calculator-leg-transfer-needs-chain-first]])) | built |
| `/options/paper` | Paper Trades (ledger table + shared detail panel. **Live unrealized P&L** — `compute.paper_trades_view(reprice=True)` reprices OPEN ledger trades via `signal_repricer` (per-spread × qty), **market-hours gated**, on reload + the 5-min manage tick; the **P&L** column shows realized (closed) or live unrealized (open), **2-decimals + green/red colored**; **Credit/Risk** show 2-decimals; headers are **Credit / Risk / P&L** (no `$`); **newest-first** default sort; **Delete / Delete-all-closed buttons are red** (needed `color=None` so `.pt-danger` beats Quasar's `bg-primary`); the **Analyze** button pops a **descriptive dialog** (verdict + rationale + unrealized P&L / % / current price / DTE / target / breakeven + close X) — `compute.analyze_paper` enriched with `rationale` + `metrics`; row-click analyses update the detail panel silently. Detail panel: the **speedometer falls back to PoP** for paper trades (was stuck at 0 — no stored composite score) and the "Underlying" label is now **"Current price"**) | built |
| `/options/captured` | Captured Signals | built |
| `/options/portfolio` | Paper Portfolio (paper account) | built |
| `/options/calculator` | Calculator (summary tiles + P&L heatmap — grid rows = **±N real chain strikes around spot** via the **Number of strikes** input (default 24, strictly around spot; `strikes_window`→`price_rows`); **intraday time-to-expiry** — the grid's first column is **"Now"** (current mark-to-market value, priced at calendar hours-to-4pm-ET /365) and the last is **"Exp"** (expiration payoff), fixing 0DTE which previously showed only the payoff everywhere; summary tiles + PoP also use the intraday "Now" T (was an `or 1/365` clamp that over-priced 0DTE ~20×); the **IV** button **implies IV from the traded contract's mark** ThinkorSwim-style via a `calc_iv` command → `cache:options:calc_iv` (`compute.calc_iv` → engine `implied_vol` bisection), falling back to ATM chain `volatility` pre-strike-pick; **multi-leg strategy builder** — a Strategy dropdown (singles + verticals credit/debit + condors iron/all-same + butterflies long/iron + calendars/diagonals) over the shared **editable leg-editor** (`leg_editor.py`: per-leg kind/side/strike/expiry/qty + Add/Remove), per-leg expiry so **calendars** price each leg at its own T, a **generic-numeric summary** for non-PCS/CCS/IC structures, and a **Copy to Simulator** button; **persists full UI state across navigation** (symbol/strategy/legs/fields/Number-of-strikes) + **auto-refreshes on return** via a single-user module snapshot — `page_state.py`; the **Symbol** field **Loads on tab-out (`focusout`) / Enter** (deduped via `inputs.should_load`; the Load button still force-reloads) with a **centered full-screen wait overlay** (`overlay.py`, `LOAD_TIMEOUT_SEC=30s` backstop) until the chain lands; the **top-level Expiry propagates to all legs** (`leg_editor.apply_expiry`, re-syncs strikes); **compact leg cells** (`leg-row`) + the **"Actions" header dropped**; **Send-to-Calculator from the Scanner now lands correctly** — `_prefill` stashes `pending_legs` + `load_symbol()` so the legs apply once the chain is loaded (applying them first wiped every strike via the leg-editor's strike-coercion — see [[calculator-leg-transfer-needs-chain-first]])) | built |
| `/options/swing` | Swing Scanner | built |
| `/options/gamma` | Gamma (GEX/Charm/DEX/Vanna bars + flip/**single Call+Put walls** + intraday heatmap; **fixed ±20-strike window** around spot for bars+heatmap (`strikes_around`, consistent candle/cell size all day; heatmap `rowsize`=median gap; heatmap cropped to the window); **blended interpolated heatmaps** (intraday **and Term**) — smooth image, no lines, dark `HEAT_STOPS` colorscale (zero→transparent like the candlestick chart), transparent bg, off-white spot line, no fade, **press-and-hold tooltip** (`_HEAT_PRESS_TOOLTIP_JS`); bar/heatmap **width split grows with session** snapshot count; **flicker-free** in-place Highcharts updates; **symbol is a dropdown** — default `$SPX`, populated from the collected universe (watchlist minus `$VIX`) via `cache:options:gamma_symbols`, **syncs to the cached symbol on build + selecting auto-refreshes + repaints ignore foreign-symbol snapshots** (no revert to `$SPX`); Term shows the **next 5 expirations regardless of cadence** (`_term_chain`); **off-hours persistence** — last session's candles+heatmap held until the next trading day's midnight CT (`active_session_date`/`gamma_cleared` + `load_date_with_grid`); off-hours `spot=None` degrades gracefully; Explain works per-selected-symbol; **Analyze** calls Claude (forced `submit_analysis` tool) and opens an **infographic** tab — regime + bias gauge, per-index price-level ladder + tiles + **what-if** (rally/sell-off/chop), bottom **"Why is this happening"**; **code-authoritative 1-day Exp. move**; also **auto-runs 4×/day** (premarket / ~18 min after open / midday / close) into per-slot keys with **Auto briefings** buttons — see the "Gamma Analyze" section below) | built |
| `/options/simulator` | Simulator (**multi-leg strategy builder** — a Strategy dropdown over the shared **editable leg-editor** (`leg_editor.py`) replaces the old single-contract selector — driving all three legacy tabs: **Replay** (re-prices the **netted** position along the underlying's recent path → stacked price + 5-Greek panels over a gap-compressed integer x-axis w/ a client-side scrub cursor) + What-if (a **dollar profit/loss payoff from entry**: P/L = position value (×100 contract multiplier) minus the **entry mark** (`whatif_baseline` = value at spot *now*) — so profit caps at the net credit, loss floors at width−credit, **matching the Calculator** — with a green profit fill above / red loss fill below breakeven (area `threshold:0` + `color`/`negativeColor`) + faint Profit/Loss washes + labels; Δt is **elapsed** days from now, per-leg decay → **calendars** correct, theta visible as Δt slides) + IV-shock; **Copy to Calculator** button; **dark-navy dashboard theme** via shared `theme.py`; **persists full UI state across navigation** (symbol/strategy/legs/sliders/active tab) + **auto-refreshes on return** via a single-user module snapshot — `page_state.py`; the **Symbol** field **Fetches the snapshot on tab-out (`focusout`) / Enter** (deduped) with the same **centered wait overlay** (`overlay.py`) until the meta lands; **compact leg cells** + no "Actions" header (shared `leg_editor`)) | built |
| `/options/expected-move` | Expected Move (candlestick price history (6-mo daily) + forward **ATM-IV expected-move cone** to the option's expiration (green/red dashed, √-time fan) + leg **strike lines** (short solid / long dashed, put/call colored) + axis **crosshair** w/ Date(X)+Price(Y) label boxes; opened in a **new browser tab** via stash-handoff from Scanner/Paper/Captured/Calculator, or standalone w/ symbol+expiry input) | built |
| `/options/rescue` | Rescue (at-risk credit spreads (PCS/CCS/IC) → **at-risk table** (paper+captured, heat-colored) → select a position → ranked **commission-aware adjustment menu**: close / partial-close / narrow / convert-IC / butterfly / roll-down/out/down-out / broken-wing / inverted / futures-hedge; each card shows gross/commission/net + metrics + legs + rationale + strategic context + warnings + score; execute cards have **Apply → confirm → `rescue_apply`** behind a stale-price guard, advisory cards show "manual"; nav badge from `cache:options:rescue_summary`) | built |
| `/sentiment` | Sentiment (two-column top: **dual** Sentiment gauges (Today + 30-Day Avg) + **dual** Market Trend gauges (Today live-intraday + 30-Day structural — directional 0–100 score, 15-min cadence) / component table; traffic-light tiles; 30d history + rolling avgs; full-width **Sector & Industry Performance** w/ Day/Week/Month %, P/C, RRG, rotation banner, **expandable industries w/ P/C+RRG**; bottom status bar; **persists across navigation**; **server-side 120s auto-refresh + bridge publish, tab-independent**) | built |
| `/sentiment/rotation` | Sector Rotation (RRG-vs-SPY: Risk-ON/OFF headline + spread; **top row** = quadrant-map table (left) + tight ROTATING FROM/INTO w/ S&P weights (right); **full-width RRG below** w/ per-sector "meteor tails" — engine `assess_sector` retains a `tail` of `TAIL_LENGTH=12` RS-Ratio/RS-Mom points sampled every `TAIL_STRIDE=2` days; page draws **one spline series per sector** (faded trail line + single bright head dot) and **hover-isolates** a sector via native Highcharts `plotOptions.series.states.inactive` (hovering one dims the rest — no client round-trip); reuses `sector_rotation_assessment`; cached, **manual Refresh only**) | built |
| `/trade` | Trade (on-demand single-symbol analysis: **Position (1–8wk)** + **Investor (months+)** Buy/Hold/Sell verdicts w/ score + top reasons + hard gates + expandable factor breakdown. The **Position** verdict is now a **backtested, IC-weighted cross-sectional factor model** (`swing_model.json` artifact → live `swing_model.py` scorer): the headline is the **validated** BUY/SELL/HOLD off a **calibration band** + an outcome line (percentile · expected fwd return / horizon · beat-SPY hit-rate) + a **"Why — validated factors"** evidence expander (per-factor z/weight/contribution/IC + model version & OOS IC), with the **legacy heuristic** verdict tucked into a collapsed expander (Investor unchanged); **MTF EMA alignment** (per-timeframe); momentum strip (RSI/ADX/MACD/VWAP/RelVol); sector strength; **Fundamentals card** (P/E/PEG/growth/ROE/margins via proxy `/instruments`); **Markov Forecast card** (third **equal-width frame in the verdict row**, alongside Position + Investor: 5-band composite-score Markov chain → stacked-area band-probability forecast + P(BUY)/P(SELL)/E[score] at 5/10/20d + a bounded confidence-weighted drift-tilt `markov_adjusted_score` headline, verdict label unchanged; **chart plots the dense near-term `trajectory` now/1/2/3/5/10/20d** so it differs by score — the 5/10/20d tail converges to the bull-leaning prior stationary; chart is dark-navy themed); **dark-navy "dashboard" theme** (`.calc-v2` via shared `theme.py`, `items-start` compact cards); **tab-out (`focusout`) = Analyze** (deduped); **persists last analyzed symbol** + analysis across nav) | built |
| `/driver` | Driver (**autonomous monitor + override** [level B]: a **Claude decision layer** (Opus 4.8 default; `DRIVER_MODEL` env / `shared/driver_model.txt` override → e.g. Sonnet 4.6) auto-selects/sizes **defined-risk option spreads (PCS/CCS/IC) from the scanner** (`cache:options:scan`) toward **net $500/day** in **paper**, gated by a **`cache:driver:control`** master switch + confirm-gated **STOP** kill-switch; the page shows day-P&L-vs-$500 progress, open-driver-positions, a newest-first **decision-log** audit (`cache:driver:autonomous`, times in **CST**), and a **Performance scorecard** (win-rate / profit-factor / avg win-loss / P&L by symbol & strategy — `cache:options:driver_paper_perf`), all reading the Driver's **own isolated paper book** (`cache:options:driver_paper_account`, separate from the manual account), with **Enable/Disable** + **Run now**; 09:28-ET + 30-min-RTH checkpoints run `build_packet`→`decider.decide`→**`guardrails.apply_guardrails`** (PURE code clamps size + halts at banked-$500/loss-cap/VIX — the model never sizes its own risk)→`cmd:options` **`driver_paper_create`** (opens into the dedicated `paper_account_driver.db`, repriced + auto-exited on the 5-min manage tick — fully separate from the user's manual paper trades). **Legacy** morning-agent **order-approval queue** retained (gated off while autonomy is enabled): Run morning agent → graded day + proposed trades; **APPROVE** (confirm dialog) / **SKIP**; conditions strip + grade rationale; **Performance** view (win-rate / P&L-by-bucket + trade table; **today-only** decision log; perf **P&L colored** green/red; **Bucket / Instrument** full-word headers; **sticky table headers**). Orders simulated (`PAPER_TRADE=True`). **Root-cause fix (2026-06-27): the driver had NEVER opened a position** — `compute.open_driver_position` read `signal_id`/`strategy`/`entry_credit` but the driver feeds RAW scanner signals keyed `id`/`type`/`credit`, so every open `KeyError`'d on `'signal_id'` and the defensive `try/except` swallowed it to `status=error`; the decision log showed "executed" (only the ENQUEUE) while the account stayed empty. Fixed by normalizing the signal shape — open positions now appear + the scorecard P&L populates. See [[driver-feeds-raw-scanner-signal-shape]]) | built |
| `/settings` | Settings (GUI prefs via `app_settings`: scanner **audio alert** on/off + sound + volume, only-during-market-hours, min-score-to-alert; desktop-notification toggle + permission grant + Test sound. Extensible — first batch) | built |
| `/portfolio` | Portfolio (3-tier, `services/portfolio_svc` :8212: **Holdings / Sectors / Performance** tabs over the portfolio model — sector breakdown, vs-sector RS, since-purchase excess, benchmark over/under-weight, tailwind; **Performance** scorecard (return/capital/risk/entry grades + composite + ann. return + drawdown) with a per-position **advisory suggestions** detail pane; **live-streaming P&L** via the service's proxy SSE consumer republishing each tick; proxy/stream status bar; persists across nav) | built |
| `/eod` · `/eod/detail` | EOD Report (pure-webgui aggregator over `options:*` + `driver:*` caches. **Summary** = headline tiles + a **verbose Daily / Weekly(WTD) / MTD performance** block **per book** — the manual paper **ledger** (`options:paper_trades`) and the **Driver** account (`options:driver_paper_account`, incl. its new `closed_positions`) shown separately (realized P&L bucketed by **exit** date; opened/credit by **entry** date; a per-book now-line = equity/session-P&L/open-unrealized/open-count). **Detailed** = the same performance + **trade-type breakdowns** (by **strategy** PCS/CCS/IC, by **0-DTE/Swing**, by **status** Open/Closed/Expired) for each book + full trade/scanner/captured/driver tables. **Navigation**: a jump-link **TOC** + every section in a native **`<details>`** (collapsible, **no JS** — works in-app AND in the exported files). **Generate** snapshots the caches → standalone `summary.html` + `detail.html` archived under `webgui/data/eod/<date>/`; `/eod/file` serves them raw. Pure builders (`normalize_trades`/`period_buckets`/`breakdown_rows`/`performance_table_html`/`breakdown_table_html`/`toc`/`details_section`) unit-tested. Realized reads `$0`/`—` until trades close — by design, not a bug) | built |
| `/status` | System Status (pure-webgui health board: overall up/down banner + per-component cards probing **Memurai** PING, **schwab-proxy** `/health`, **Schwab Authorization** (OAuth token state, with an **Authorize** button → proxy `/auth`), the **five domain services** `/health`, and **webgui** itself; plus a **published-data-freshness** table — each domain's cache version + age, flagging stale scheduled views; **per-component Restart button on offline cards** — proxy/services relaunch via `tools\restart_one.bat`, Memurai via `Start-Service`; off-thread sweep, auto-refresh 15 s + manual) | built |
| `/terminate` | Terminate (guarded "stop the whole local stack" page: red **Stop all services** button behind a confirm dialog → spawns `stop_all.bat` detached via `cmd /c start`, which kills the proxy + 5 services + this web app by listening port; **Memurai is left running**; the page goes unresponsive after confirm, by design) | built |

The `pages/options/` subpackage shares `header.py` (compact quotes/VIX/sentiment
strip), `detail.py` (collapsible Trade detail panel, reused by all signal
tables), `svg.py` (gradient-bar / range-marker SVG — the composite-score
speedometer is now the shared Highcharts gauge in `pages/gauge.py`), `inputs.py`
(`select_all_on_focus` + `should_load` symbol-input helpers — `should_load` dedups
the symbol tab-out/Enter Load trigger), **`overlay.py`** (the shared full-screen
**wait overlay** — `build_loading_overlay()` → a handle with `.show(msg)`/`.hide()`,
plus a shared `LOAD_TIMEOUT_SEC` backstop; both the Calculator + Simulator show it
centered while a symbol Loads/Fetches), **`strategies.py`** (PURE shared
strategy/leg model — the normalized leg dict + `STRATEGY_TEMPLATES`/`STRATEGY_GROUPS`
+ `build_default_legs` + analytic-vs-numeric `summary_code`; imported by **both** the
Calculator and Simulator so templates never drift), **`leg_editor.py`** (the shared
**editable multi-leg-table widget** both pages mount — `state['legs']` is the source
of truth, each page injects its own `strikes_for`/`expiries_for` + `show_premium`;
`apply_expiry(expiry)` propagates the Calculator's top-level Expiry to **all** legs;
the header table drops the "Actions" label and renders **compact `leg-row` cells**),
and **`handoff.py`** (cross-page
signal hand-off — Scanner/Swing "Send to Calculator" via a module-level `_pending`
stash + "Send to Paper trade" which enqueues a `paper_create` command on
`cmd:options`, plus the shared `add_row_actions` per-row action-button slot, **and the
Simulator↔Calculator leg-copy stashes** (`send_to_simulator`/`send_to_calculator_legs`
+ `take_pending_simulator`/`take_pending_calculator_legs`); engine-free),
**`strategy_menu.py`** (the shared cascading **Strategy picker** — a
`ui.select`-compatible button → nested family→variant Quasar submenu driven by
`strategies.STRATEGY_MENU`/`strategy_label`; both pages mount it so the picker
never drifts; `boxed=True` styles the trigger for the navy theme), and
**`theme.py`** (the shared dark-navy **"dashboard" theme** — one page-scoped
`DASHBOARD_CSS` string the Calculator **and** Simulator both inject and wrap in
`.calc-v2`: filled navy input boxes, bordered `calc-card`s, `cv2-btn`/
`cv2-btn-primary` buttons, boxed Strategy button, header-table legs, dark
transparent tabs so the dark Highcharts panels sit on the navy, and the
teleported `strat-menu-navy` popup — so the two pages never drift), and
**`page_state.py`** (the shared PURE persistence helpers — `snapshot` /
`merge_restore` / `pick_seed` — both pages use to restore their full UI state across
navigation via a single-user module snapshot; see the route table). Options design + plan: [`docs/plans/2026-06-14-options-section-expansion-design.md`](docs/plans/2026-06-14-options-section-expansion-design.md)
/ [`-plan.md`](docs/plans/2026-06-14-options-section-expansion-plan.md).
Gamma/Simulator: [`docs/plans/2026-06-14-gamma-simulator-design.md`](docs/plans/2026-06-14-gamma-simulator-design.md) / [`-plan.md`](docs/plans/2026-06-14-gamma-simulator-plan.md).

**App theme — dark-navy "dashboard" (DONE — 2026-06-24; the canonical reference).**
> ⚠️ **Migrating to Tailwind-first** (see "UI styling standard — Tailwind-first"
> below + the design doc
> [`docs/plans/2026-06-28-tailwind-first-ui-migration-design.md`](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md)).
> The `DASHBOARD_CSS` semantic classes below (`.calc-card`/`.cv2-btn*`/`.calc-eyebrow`)
> are being replaced by **Tailwind-class-string token constants** in `theme.py`
> applied via `.classes(CARD)`; only the **Quasar-internal** rules (`q-field__control`,
> `q-tab*`, the teleported `.strat-menu-navy` popup) stay in `ui.add_css`. The palette
> below is the canonical hex source the tokens encode.

The shared dark-navy look used by the **Calculator** + **Simulator** (page-scoped;
promotable app-wide). One CSS string lives in **`webgui/pages/options/theme.py`**
(`DASHBOARD_CSS`, scoped under `.calc-v2`); a page injects it with
`ui.add_css(DASHBOARD_CSS)` and wraps its content in `.calc-v2`, so the two pages
never drift. **This section + `theme.py` are the single source — look here to apply
or change the theme.**
- **Apply to a new page:**
  ```python
  from pages.options.theme import DASHBOARD_CSS
  ui.add_css(DASHBOARD_CSS)
  with ui.column().classes("calc-v2 w-full gap-4"):
      ui.label("Title").classes("text-h6 text-[#eaf0fb]")       # Tailwind, not .style()
      with ui.column().classes("calc-card w-full gap-3"):      # bordered navy panel
          ui.input("Symbol")                                    # auto-boxed (q-field)
          ui.button("Go", color=None).props("no-caps").classes("cv2-btn-primary")
  ```
  Inputs / selects / tabs inside `.calc-v2` are auto-restyled; **buttons need
  `color=None`** (drops Quasar's `bg-primary`) + a `cv2-btn` / `cv2-btn-primary` class.
- **Class vocabulary** (all under `.calc-v2` except the popup): `.calc-v2` page wrapper
  (navy radial-gradient bg + base text) · `.calc-card` bordered navy panel ·
  `.calc-eyebrow` small muted label · `.cv2-btn` / `.cv2-btn-primary` secondary /
  primary button · `.strategy-menu-btn` boxed Strategy trigger
  (`strategy_menu.build_strategy_menu(..., boxed=True)`) · `.strat-menu-navy` the
  teleported Strategy-menu popup (**GLOBAL** — Quasar menus mount on `<body>`, outside
  `.calc-v2`) · `.leg-head` leg-table header row
  (`leg_editor.build_leg_editor(..., header=True)`) · `.leg-strike` centered strike cell.
- **Palette** (hex → role): page bg `#16243f→#0c1424→#0a0f1c` (radial) / border `#1d2942`
  · card `#101a30` / border `#213152` · input box `#0c1426` / border `#243353` / focus
  ring `#3b82f6` · input text `#e7edf8` · base text `#cdd8ee` · muted label `#7f8db0` ·
  icon + eyebrow `#8794b4` · title `#eaf0fb` · secondary btn `#15213b` (hover `#1b2950`)
  · primary btn `#2563eb` (hover `#1d4fd1`) · tab active `#e7edf8` / inactive `#8794b4`
  / indicator `#3b82f6` · **P/L payoff** profit-green `#34d399` / loss-red `#f87171`
  (gradient fills + faint `rgba(...,.06)` washes — `simulator.whatif_figure`).

**UI styling standard — Tailwind-first (mandatory for all NiceGUI UI).** All
component styling MUST be expressed as **Tailwind utility classes via `.classes()`**.
This is a hard standard — every new page, and every page touched during the migration,
follows it. Scope decided **pragmatic** + intent **convert + light polish** (preserve
today's dark-navy look, standardize it into the token vocabulary, fix obvious
inconsistencies as each screen is converted — no gratuitous redesign). The five rules:
- **Banned:** `.style(...)`, inline `style=` attribute strings, `.props("style=…")`, and
  fixed pixel measurements outside Tailwind classes. Use Tailwind scale utilities, or
  `[...]` **arbitrary values** when an exact value is required (`w-[37px]`,
  `text-[#eaf0fb]`) — never `.style("width:37px")`.
- **Dynamic / data-driven values → MAP TO FIXED PALETTE CLASSES.** NiceGUI 3.x bundles
  the **Tailwind browser JIT** (`tailwindcss.min.js`), so a runtime-built arbitrary-value
  class (`.classes(f"text-[{hex}]")`) *does* generate (verified) — but we deliberately do
  **NOT** do that. A data-driven color/size is mapped from its **known finite set** to a
  **static, semantic** Tailwind class via a small pure lookup — a regime/score *state* →
  `text-emerald-400` / `text-amber-400` / `text-rose-400`; a collapsed/expanded width →
  `w-11` / `w-[360px]` — with a neutral-class fallback, so the vocabulary stays clean and
  deduped (no scattered magic hexes). Prefer mapping a semantic state the payload **already
  carries** (e.g. a `label`/`bias`) page-side; only when no clean state exists, refactor the
  **Tier-2** source to emit one (allowed — this is the documented exception to webgui-only).
  NEVER set the dynamic value via `.style()`. (Exception: a **genuinely continuous** value with
  no finite set — e.g. a computed `flex-grow` ratio — may use a **runtime arbitrary-value class**
  (`flex-[{w}_1_0%]`, JIT-generated) reset via `.classes(remove=prev, add=new)`; this is distinct
  from data-driven COLORS, which always map to a fixed finite palette.)
- **Design tokens, NOT semantic CSS classes.** The dark-navy theme is a vocabulary of
  **Python Tailwind-class-string constants** in `webgui/pages/options/theme.py`
  (`PAGE` / `CARD` / `EYEBROW` / `BTN` / `BTN_PRIMARY` / `LABEL` / `MUTED` / …), each a
  reusable utility string encoding the palette above, applied with `.classes(CARD)`.
  No `.calc-card { … }`-style CSS rules. (Tailwind ships bundled in NiceGUI; custom
  theme colors aren't trivially configurable, so tokens carry `[#hex]` arbitrary values.)
- **The ONE escape hatch.** A single documented `ui.add_css` block per theme is allowed
  **only** for Quasar-internal / teleported DOM that component `.classes()` physically
  cannot reach: `q-field__control` field internals (boxed inputs), `q-tab*`, the
  `.nicegui-expansion-content` gap, and body-mounted popups like `.strat-menu-navy`.
  Nothing else belongs in `ui.add_css`.
- **Out of scope** (NOT NiceGUI components, so the rule doesn't bind them): standalone
  documents served as raw `HTMLResponse` — EOD `summary/detail.html`, the Gamma
  Explain/Analyze infographics — **raw `ui.html()` HTML-string fragments** built with inline
  `style=` attributes (e.g. the Calculator P&L heatmap grid, the Gamma Explain blocks), since
  they aren't NiceGUI components with `.classes()` — and **Highcharts option dicts** (chart
  colors are chart config, not CSS).

The migration runs in **phases (menu first, then each screen by logical group)** — see
the design doc
[`docs/plans/2026-06-28-tailwind-first-ui-migration-design.md`](docs/plans/2026-06-28-tailwind-first-ui-migration-design.md).
Status snapshot: **Phase 0 + 1 + 2 + 3a + 3b done** (2026-06-28). **P0** — `theme.py` ships the
Tailwind token vocabulary (`PAGE`/`CARD`/`EYEBROW`/`LABEL`/`MUTED`/`BTN`/`BTN_PRIMARY`/
`STRATEGY_BTN` + the semantic state-color tokens `TXT_POS`/`TXT_WARN`/`TXT_NEG`/
`TXT_NEUTRAL` + `STATE_TEXT_CLASSES` + the 3D-button tokens `BTN_3D`/`BTN_3D_DANGER`) + a
`QUASAR_INTERNAL_CSS` escape-hatch block alongside the still-intact legacy `DASHBOARD_CSS`
(additive — its `.calc-card`/`.cv2-btn*` rules stay until their last consumer flips in the
Phase 4 cleanup). **P1** — the **nav shell** (`main.py`) is fully Tailwind; `_NAV_CSS` is now
Quasar-internal-only. **P2** — the shared **`pages/options/*` helpers**
(`detail.py`/`header.py`/`overlay.py`) are `.style()`-free: dynamic data-driven colors are
**palette-mapped** (a finite state/label → a fixed token — detail tiles via `TXT_*`; the
header VIX-regime badge + sentiment dot via local label→class maps
`regime_badge_class`/`sentiment_dot_class`), and reactive recolors use
`.classes(remove=…, add=…)` to avoid class accumulation; `leg_editor.py`/`strategy_menu.py`
were already inline-style-free. **P3a** — the six **signal-table screens**
(`scanner`/`swing`/`captured`/`paper`/`portfolio`/`rescue`) are free of `.style()` AND every
Vue `:style=` slot binding: dynamic **table-cell** colors stamp a Tailwind **class** field
from a finite-set map (`score_zone_class`/`rec_class`/`pnl_class`/`verdict_class`/
`heat_bg_class`/`heat_border_class`/`cash_class`) and bind `:class` (JIT-generated); the **3D
gradient buttons** use `BTN_3D`/`BTN_3D_DANGER` (`color=None`); per-page `ui.add_css` is
slimmed to Quasar-table-internals (cell padding, sticky `thead`, `.q-table__middle`, scanner
`.q-tab*`). A `test_no_inline_style.py` guard pins all helper + 3a pages. **P3b** — the
**Calculator + Simulator** (the heaviest `DASHBOARD_CSS` consumers) now use the tokens:
`.calc-card`→`CARD`, `.cv2-btn`→`BTN`, `.cv2-btn-primary`→`BTN_PRIMARY`, `.calc-eyebrow`→
`EYEBROW`, `.strategy-menu-btn`→`STRATEGY_BTN` (in the shared `strategy_menu.py`), title →
`LABEL`, calc summary tiles palette-mapped via `tile_color_class`; both pages now inject
`QUASAR_INTERNAL_CSS` (NOT `DASHBOARD_CSS`) and keep `.calc-v2`/`.strategy-menu-btn`/`.leg-*`
ONLY as **scope hooks** for the Quasar field/tab/menu internals; the dead `CALC_CSS` was
deleted. The Calculator **P&L heatmap is a raw `ui.html()` grid → out of scope** (documented).
**`DASHBOARD_CSS` is now consumed ONLY by `trade.py`** — Phase 4 flips Trade, then the cleanup
deletes the now-dead `.calc-card`/`.cv2-btn*` semantic rules. **Next: Phase 3c** (chart-heavy:
Gamma + Expected-Move). Update this line as phases land.

**`pages/ui_guard.py` (cross-cutting, load-bearing — used by ~15 pages).** Provides
`guard` / `guard_async` decorators that make a NiceGUI callback a clean no-op when
the owning client/slot has been deleted (browser tab navigated away / closed /
reconnected) — swallowing the `RuntimeError('… has been deleted.')` that `ui.timer`
and post-`await` event handlers otherwise raise (and that NiceGUI's `handle_exception`
re-raises, doubling the noise). Wrap every timer callback and `on_click`/`.on(...)`
handler that mutates page widgets in it.

## webgui development notes (read before adding a page)

**Page pattern.** Add a leaf module `webgui/pages/<name>.py` exposing `render()`.
In `webgui/main.py`, add a `@ui.page("/route")` that does `with _layout(active,
title): from pages import <name>; <name>.render()`, and add the item to `NAV`
(flat) — `_layout` handles header, drawer, and the proxy-down banner. Register
the route in `test_shell.py`'s expected set.

**Import an app's engine (sys.path glue).** App folders have hyphens / no package
init, so a page adds the app dir to `sys.path` then imports the module by name —
e.g. `from repo_paths import TRADE_ANALYZER; sys.path.insert(0,
str(TRADE_ANALYZER))` then `import <engine>`. `webgui/conftest.py` already puts
the repo root + `webgui` on `sys.path` for tests. The proxy client is
`proxy.schwab_py_client` (schwab-py compatible) and `proxy.schwab_client`
(SchwabClient compatible).

> **Cross-app module-name collisions (IMPORTANT, bitten us).** Putting multiple
> app dirs on `sys.path` means same-named top-level modules clash process-wide
> (one `sys.modules` entry wins). Known clashes: **`scoring`** (options-scanner
> `scoring.py` vs sentiment-dashboard `scoring/` package) and **`notifier`**.
> Once the Sentiment page loads, `import scoring` resolves to sentiment's, which
> broke `scanner_engine.run_full_scan`'s lazy `from scoring import …`. Mitigation:
> `pages/options/engines.py` `options_scoring()` context manager pins the options
> `scoring` for the duration of an options engine call and restores after — used
> in `scanner.py`/`swing.py`. When wiring Trade/Portfolio/Driver, watch for the
> same trap (e.g. `notifier`); prefer importing engine deps eagerly at module
> load (binds the name once) and/or wrap lazy engine calls similarly.

> **Stdlib collisions via the script-launch path (IMPORTANT, bitten us 2026-06-24).**
> A service's OWN dir lands on `sys.path` when its `app.py` runs **as a script**
> (`python services/<svc>/app.py`), so a module there named after a **Python stdlib
> module** shadows it process-wide. `services/driver_svc/secrets.py` (an API-key
> resolver) shadowed the stdlib `secrets`, so starlette's `from secrets import
> token_hex` (pulled in by FastAPI) crashed `driver_svc` **on launch** — but NOT in
> tests (pytest runs from the repo root, a different `sys.path`, so the suite was
> green while the service couldn't start). Fixed by renaming it to `api_keys.py`;
> `driver_svc/tests/test_api_keys.py::test_no_module_shadows_stdlib` now guards every
> service module name against `sys.stdlib_module_names`. **Rule:** never name a
> service module after a stdlib module (`secrets`/`token`/`types`/`queue`/`select`/…).

**Structure for testability.** Keep pure transforms/figure-builders as
module-level functions (TDD them with sample dicts); keep `render()` thin
(widgets + wiring). Heavy/blocking engine calls go through
`await nicegui.run.io_bound(fn, ...)` with a spinner + try/except → `ui.notify`.

**NiceGUI gotchas (learned, costly):**
- `ui.html(...)` **strips `<style>` and `<iframe>`**. For CSS use `ui.add_css(css)`
  (rules only, scope with a class); render HTML *fragments*, not full documents.
  See `pages/options/gamma.py` Explain (`EXPLAIN_CSS` + `wrap_explain`).
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
  instance). See `pages/options/simulator.py`.
- Charts: **Highcharts** via `ui.highchart(options)` (the `nicegui-highcharts`
  element) — NOT Plotly. Build the options dict in a pure function so it's
  unit-testable; update in place with `el.options = fig; el.update()` (replaces the
  old `update_figure`). Gauges are the shared `pages/gauge.py` angular gauge
  (painted red→yellow→green rainbow face + needle). Heatmaps/bars in Gamma,
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
  trading_days_only=True)`). A `ui.highchart` added DYNAMICALLY on a page
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
  Drop `plotBackgroundColor` (the mesh) and set `borderWidth:0`. **`states:{inactive:
  {enabled:False},hover:{enabled:False}}`** stops the hover-dim/fade.
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
- Tables: `ui.table(columns=[{name,label,field,...}], rows=[...], row_key="id")`;
  selection via `selection="single"` + `table.selected`; row click via
  `table.on("rowClick", handler)` where `event.args[1]` is the row dict.
- Number/select/slider/toggle fire `on_value_change`. Set values with
  `el.value = ...; el.update()`. Auto-refresh + autoload via `ui.timer(secs, fn)`
  and `ui.timer(0.1, fn, once=True)` (see Gamma).
- A page is built per request inside `_layout`; keep page state in a local dict
  closure, not module globals.

**Verify in the browser.** `.claude/launch.json` defines the `webgui` dev server
on **:8500** (`autoPort:false` — the NiceGUI port is fixed). Use the Claude
Preview tool (start `webgui`, screenshot). Restart the preview after code changes
to pick them up. To drive Quasar inputs from the preview, set the native value +
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

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (552 green as of
this writing). TDD pure functions; smoke-verify `render()` with a screenshot.

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

**Sentiment (`/sentiment`) — DONE.** `webgui/pages/sentiment.py` reuses the
copied `history_backfill.backfill_history(...)` engine (latest completed-session
composite + 30d history) + `scoring` (`composite.velocity/divergence`,
`trend_regime.classify/commit_state`) + the ported `sectors_ref.load_sectors_data`.
**Layout:** a two-column top region — left: **two** Market Sentiment speedometers
(**Today** `gauge_score(total)` + **30-Day Avg** `gauge_score(sentiment_30d_avg(snaps))`,
the avg = page-side mean of the history composites) + bias + size/conf; right: **two**
**Market Trend** speedometers (**Today** + **30-Day**, both via `trend_gauge_value`,
which now returns the directional 0–100 trend **score directly** — no anchor/nudge).
Both values come from `derived["trend"]` / `derived["trend_30d_ago"]` **published by
`sentiment_svc`** via the new **intraday Market Trend model** (see the dedicated
section below) — with the regime badge/desc beneath and a **TREND DETAIL**
press-and-hold popup showing the four sub-scores (Price/Breadth/Sector/VIX) +
confidences (`trend_subscore_rows`). The top region is now
**three columns** (Market Sentiment / Market Trend / Signals); the component
**table** (Value/Score[2dp]/Weight/Conf — Contrib computed for reconciliation but
not shown; credit_pulse excluded per v4.3 `WEIGHTS`) and the Trend detail are
**press-and-hold popups** (`ui.menu().props("no-parent-event")`), not always-visible
columns. The Signals column is a **2×2 four-tile matrix** (`TILE_DEFS` =
**Bias/Signal/Yesterday/Change** — Modifier dropped per design) with a
**traffic-light background** (`traffic_color(total)`). Below that, a **collapsed**
`ui.expansion("30-Day History")` holds the 30d Highcharts history (soft grid) + 5d/20d
rolling averages + velocity/divergence, then the full-width **Sector & Industry Performance** table
(11 sectors × Day/Week/Month %, P/C, RRG; per-cell colored; subtle gridlines + row
hover via a `.sent-sectors` `ui.add_css` block) with a rotation banner
(`scoring.rotation.compute_rotation`) + "% green | Cap-wtd | Score" summary, and a
bottom **status bar** (Updated/Next/Sectors/Proxy — proxy checked off-thread in
`load()`, cached, not on the status timer). Each sector **expands** into its industry
sub-rows (▷ toggle or Expand/Collapse All; lazy-fetched via `_load_industries` +
cached; industries show Day/Week/Month % **and P/C + RRG**).
The sector load (`_load_sector_perf`, ~24 proxy calls incl. 11 `/chains` for P/C)
runs at startup + on manual Refresh. **Auto-refresh is server-side and
tab-independent:** a module-level background task (`start_background_refresh` →
`refresh_cache`, started from `main.py` `@app.on_startup`, mirroring
`scanner.start_autoscan`) updates `_CACHE` (composite-only) **and republishes the
bridge every 120 s** regardless of any open/active tab — even with no browser open.
The page **never fetches on activation**: it paints from `_CACHE` instantly and a
fetch-free `ui.timer(120, _repaint_from_cache)` tracks the background cache;
**manual Refresh** is the only page-driven fetch. Persists across navigation via
`_CACHE` (single-user), incl. expanded sectors. Verified against the live proxy to
match the source dashboard exactly (82% green | Cap-wtd +0.70% | Score 7.8/10; real
industry rows on expand). Designs/plans:
[base](docs/plans/2026-06-14-sentiment-page-design.md) /
[sector-perf](docs/plans/2026-06-14-sentiment-sector-perf-design.md) /
[persistence+industries](docs/plans/2026-06-14-sentiment-persistence-industries-design.md)
(+ matching `-plan.md` files).
**Intraday Market Trend model (DONE — 2026-06-19).** The Market Trend panel is
driven by a **directional 0–100 score** (50 = neutral, 100 = max bull) recomputed
**every 15 min**, replacing the old slow daily 5-state SPY classifier. Pieces:
- **Pure scoring** `sentiment-dashboard/scoring/intraday_trend.py` (scalar in/out, no
  I/O): four directional sub-scores — `score_price` (45%: MTF EMA alignment + VWAP +
  MACD/RSI, **ADX-scaled** so chop hugs 50), `score_breadth_dir` (25%: A/D + %>50DMA +
  H/L), `score_sector_participation` (20%: # green + cyclical-vs-defensive day%-spread),
  `score_vix_context` (10%: level/change/term) + `vol_confidence_factor` (a VIX-spike
  damper on aggregate confidence) — blended by `blend_trend` (confidence-weighted, same
  idiom as `composite.blend`). `score_to_state` maps the score → the existing 5-state
  vocabulary (**80 bull / 70 pullback / 30–70 range / 20 bear_rally / bear**); hysteresis
  reuses `trend_regime.commit_state` (2 reads to flip). `TrendSub` is a local float
  dataclass (NOT the int-only `ScoreResult`).
- **Service compute** `services/sentiment_svc/compute.py`: `compute_intraday_trend`
  (fetches SPY intraday 5/15-min + daily via the proxy's new
  `get_intraday_history`, breadth/sector/VIX quotes — reuses `live_composite._BREADTH/_last/
  _VIX_SYMS` and standalone `technical`) and `compute_30d_trend` (daily structural analog
  for the **second gauge**, price + sector only). Both defensive → neutral on any failure.
- **15-min cadence + persisted state** in `handlers.refresh` via the module-level
  `_TREND` holder (lock-guarded; `scheduler.trend_due`/`TREND_INTERVAL_SEC=900`): the
  EMA-smoothing + hysteresis state thread across reads; the held trend rides inside the
  existing `cache:sentiment:composite` `derived.trend`/`derived.trend_30d_ago` (no new
  Redis key).
- **Bridge** (`live_composite.build_bridge_payload` + `compute._bridge_trend`): the
  intraday `state`/`confidence` + additive `trend_score`/`sub_scores` are merged onto the
  daily `classify` `sma_*`/`drawdown` (kept for the additive-only contract). `regime_filter`
  reads `state`/`confidence` unchanged (state strings + vote map identical). The standalone
  `publish_bridge` GEX path stays on the daily classify.
- **Page** `webgui/pages/sentiment.py`: `trend_gauge_value` returns the score directly;
  `trend_subscore_rows` feeds the TREND DETAIL popup. Verified live end-to-end (compute →
  Redis → bridge → rendered gauges + popup). Design/plan:
  [design](docs/plans/2026-06-19-intraday-market-trend-redesign-design.md) /
  [plan](docs/plans/2026-06-19-intraday-market-trend-redesign-plan.md).

**Live intraday + bridge (DONE).** `sentiment-dashboard/live_composite.py`
`compute_live(schwab, sector_data)` computes a **live** composite from current
quotes reusing the pure scoring modules (the live analog of
`history_backfill._score_one_day`); `build_bridge_payload(...)` + `bridge.write_bridge`
publish `shared/sentiment_bridge.json` (consumed by `options-scanner/regime_filter`).
The **GEX collector** (`options-scanner/gex_collector.py`) publishes the bridge each
5-min cycle via a **subprocess** (`publish_bridge.py`, sentiment dir on `sys.path[0]`
to dodge the `scoring` package-vs-module collision) — independent of the webgui. The
webgui page's **headline always uses the live composite** (`compute_live`; the
`is_rth` flag now only labels the date as "live intraday" vs "latest — market
closed"), falling back to the backfill snapshot only if the live compute fails;
**backfill feeds only the 30-day history chart**. This keeps the web matched to
the legacy v4.3 methodology around the clock — Put/Call uses cap-weighted sector
P/C (not `$CPCE`) and Rotation uses dual-momentum (not the blended `compute_rotation`);
off-hours, Put/Call/Breadth may read 0 when there's no option/market volume, exactly
as the legacy "Fetch Live" does. Component labels match the legacy
("Put/Call (sectors)", "Market Breadth", "Sector Performance"; VIX value `T{t}-1D{d}-S{s}`).
The page also publishes the bridge on each load. Verified live: `compute_live`
reproduces the legacy component scores (VIX T8-1D1-S3=5, Breadth 10, Rotation 7
dual-momentum) and `regime_filter.evaluate_regime()` reads the written bridge.
Design/plans: [live-bridge](docs/plans/2026-06-14-live-sentiment-bridge-design.md) +
[web/legacy reconcile](docs/plans/2026-06-15-sentiment-web-legacy-reconcile-design.md)
(+ `-plan.md` files).

**Next session — remaining pages (Phase 3.3–3.5 of the webgui plan):**
- **Trade** (`/trade`): **DONE — 2026-06-16 (3-tier, `services/trade_svc` :8213).**
  See "Trade page (`/trade`) — DONE" below.
- **Portfolio** (`/portfolio`): **DONE — 2026-06-16 (3-tier, `services/portfolio_svc`
  :8212).** See "Portfolio page (`/portfolio`) — DONE" below.
- **Driver** (`/driver`): **DONE — 2026-06-16 (3-tier, `services/driver_svc` :8214).**
  See "Driver page (`/driver`) — DONE" below.
- Reuse the page pattern above; verify each engine function's real signature in
  the copied module before wiring (explorations of source can drift from copy).
- Optional follow-ups: (none outstanding for the Simulator — the **Replay** tab
  was migrated 2026-06-20: `compute.sim_replay` + `sim_replay` command +
  `cache:options:sim_replay` + the page's Replay tab. See "Simulator Replay tab"
  below.) (The Gamma intraday-heatmap **collector** now runs inside `options_svc`
  — see below — so the heatmap populates all session whenever the service is up.)

**Gamma intraday-heatmap collection (DONE — 2026-06-15; expanded 2026-06-18).** Intraday GEX history
(`gex_history.db`, read by the Gamma strike×time heatmap) is now collected by the
**options service** itself, not a separate window. `services/options_svc/scheduler.py`
`gex_due()` fires once per 2-min slot within 08:30–15:20 CT on trading days (mirrors
`gex_collector`'s window/cadence); the tick runs `handlers.collect_gex_history` →
`compute.collect_gex_snapshots`, which reuses `options-scanner/gex_collector.poll_once`
(engine compute + `gex_history_db.insert_snapshot`) VERBATIM with the shared
`_proxy.schwab_py_client`. It takes the collector's advisory lock
(`data/gex_collector.lock`) so a manually-run standalone `gex_collector.py` defers.
**Symbol universe + cadence (2026-06-18):** `poll_once` iterates
`gex_collector.collection_symbols()` = the index base (`$SPX`/`$VIX`/`SPY`/`QQQ`) ∪
`watchlist.get_scan_symbols()` (`Top 20.xlsx`), deduped/order-preserving, defensive
fallback to the base on watchlist failure — so the heatmap has live data for every
watchlist symbol. The poll interval dropped **5→2 min** (`POLL_INTERVAL_MIN=2`,
`scheduler._GEX_INTERVAL_MIN=2`, `gex_status.STALE_AFTER_SEC=240` — a
`test_scheduler.py` drift-guard asserts these stay in lockstep). The Gamma page's
symbol **dropdown** reads `cache:options:gamma_symbols` (= collected universe minus
`$VIX`, `$SPX` first), published once at scheduler startup by
`handlers.publish_gamma_symbols`. Term-structure collection stays SPX-only. Design/plan:
[design](docs/plans/2026-06-18-gex-watchlist-gamma-dropdown-design.md) /
[plan](docs/plans/2026-06-18-gex-watchlist-gamma-dropdown-plan.md).
**Root cause this fixed:** previously the only writer was the standalone
`gex_collector.py` window launched by `start_all.bat`; when that window died
(closed / sleep / double-launch lock contention) collection stopped silently and the
heatmap froze at the first snapshots ("no data past the first hour"). `start_all.bat`
no longer launches a separate collector window (the standalone script remains a manual
fallback). NOTE: this path does NOT republish the sentiment bridge (the old collector
loop did); `sentiment_svc` already republishes the bridge every 120 s, so the bridge
is unaffected.
**Off-hours display persistence (2026-06-24).** Collection still stops at ~15:20 CT,
but the Gamma **display** now holds the last session's candles + heatmap until the
**next trading day's midnight CT**, then clears (Fri persists through the weekend /
holidays until the pre-session midnight). Pure helpers
`scheduler.active_session_date(now)` (today if a trading day, else the most recent
prior trading day) + `scheduler.gamma_cleared(now)` (True in the overnight 00:00–
08:30 CT window of a trading day) drive it; `gex_history_db.load_date_with_grid(conn,
symbol, view, date)` loads a prior session's rows by explicit local date
(`load_today_with_grid` now delegates to it). `compute.gamma_snapshot` returns
`None` (→ handler caches a graceful-empty view) in the cleared window and loads the
**active session date** for the heatmap; the candles re-compute from the live chain
(which off-hours returns the last session's data). DB-backed, so it survives a
service restart. **Term-structure** collection stays SPX-only, but the Gamma page's
**Term view** fetches the **next 5 expirations regardless of cadence** at render
(`compute._term_chain` widens the chain window — weekly/monthly-only names show 5
columns, not 1).

**Gamma Analyze — Claude infographic + auto-run (DONE — 2026-06-27).** The
`/options/gamma` **Analyze** button evolved from a copy-paste prompt dialog into a
live Claude call that renders an **infographic** in a new browser tab (and runs
itself four times a day). All in `services/options_svc/compute.py` (Tier-2) +
`webgui/main.py` route + `webgui/pages/options/gamma.py` (Tier-1). Pieces:
- **Forced tool-use call.** `compute.gamma_analyze(client=None, label=None)` bundles
  the live $SPX/SPY/QQQ GEX/Charm/DEX/Vanna blocks (`_gamma_blocks_for` →
  `build_summary_prompt_bundled`) and calls **Claude Sonnet 4.6** (`_ANALYZE_MODEL`,
  `thinking={"type":"disabled"}`, `max_tokens=1500`) forcing the **`submit_analysis`**
  tool (`_ANALYZE_TOOL`) — the reply is one structured tool_use block, never free
  text. `_parse_analysis` normalizes it (total over adversarial input, mirrors
  `decider.parse_decision`). The `anthropic` import is LAZY; the key resolves in
  `compute._anthropic_api_key` (env `ANTHROPIC_API_KEY` → gitignored
  `shared/anthropic_key.txt`) — **options_svc does NOT import driver_svc** (kept local
  to avoid the cross-app collision).
- **Infographic render (pure, testable).** `analyze_infographic_html(data, subtitle)`
  → a regime banner + **bias meter** (`_bias_meter_html`, −100…+100, sign-colored
  marker); a **per-index card** (`_index_card_html`) = a **price-level ladder**
  (`_ladder_svg`: spot vs gamma flip / call+put walls / expected-move band, with
  **label de-collision** so clustered levels stay readable) + **metric tiles**
  (`_metric_tiles_html`) + note + a **per-symbol what-if** (`_whatif_html`: ▲ rally /
  ▼ sell-off / ▬ chop); and a bottom **"Why is this happening"** section. Wrapped by
  `_analyze_doc` (the standalone dark doc + `_ANALYZE_CSS`). Output carries **no
  disclaimers** (system-prompt-enforced).
- **Code-authoritative Exp. move.** The engine's `calc_expected_move_from_chain` is a
  **0-DTE remaining-hours-to-close** EM (`hours_left` clamps to 0.1h off-hours / at the
  close → collapses to ~0; the bug that surfaced SPX EM ≈ 3). `compute._session_expected_move`
  computes a stable **1-day** EM (`spot · ATM_IV · √(1/365)`, reusing the engine's
  static `_find_nearest_exp_key`/`_get_atm_iv`) — used both in the prompt and as a
  per-symbol **override** of the model's copied value, so the displayed EM is
  engine-computed, not AI-echoed.
- **4×/day auto-run.** `scheduler.analyze_slot_due(now, ran_slots)` fires once per
  trading day per slot (CT: premarket 08:00 / open 08:48 [~18 min after the 09:30 ET
  open] / midday 11:30 / close 14:58 → 09:00/09:48/12:30/15:58 ET) within a 20-min
  grace (tolerates a missed tick / mid-window start, no stale backfill); the loop
  latches `analyze_ran` BEFORE the blocking call so a slow call can't double-fire.
  `handlers.run_scheduled_gamma_analyze(bus, slot)` runs `gamma_analyze(label=…)` and
  caches under that slot's **own key** (`CACHE_GAMMA_ANALYZE_SCHED` =
  `cache:options:gamma_analyze_{premarket,open,midday,close}`) — **separate** from the
  ad-hoc `cache:options:gamma_analyze` so a scheduled run never trips the page's
  `_watch_analyze` (which auto-opens a tab). The doc subtitle is stamped with the slot
  + CT time.
- **Serving + page.** `webgui/main.py` `@app.get("/options/analyze")` serves the cached
  HTML raw (`analyze_html`); `?slot=premarket|open|midday|close` (`analyze_view_for`)
  serves the auto-briefings, no slot → the ad-hoc result (mirrors `/options/explain`).
  `gamma.py` `_watch_analyze` opens `/options/analyze?v=<version>` in a new tab on the
  version-poll (like `_watch_explain`); a row of **Auto briefings** buttons opens each
  slot's `/options/analyze?slot=…` (enabled once that slot's version is present).
- **Graceful degradation everywhere** — no live chains (market closed) / no API key /
  API error / no tool reply each return a readable HTML page so the tab always opens.
- Tests: `services/options_svc/tests/{test_compute,test_handlers,test_scheduler}.py`
  (tool-use render, EM override, parse defensiveness, slot cadence, scheduled-cache
  isolation) + `webgui/tests/test_analyze_route.py`. Verified live end-to-end (real
  Claude call → infographic; EM SPX 2.96→45.9 / SPY 0.27→4.22 / QQQ 0.52→8.0).

**Options GUI polish batch (DONE — 2026-06-16).** A set of UI/UX fixes across the
Options section (design/plan:
[design](docs/plans/2026-06-16-options-gui-polish-design.md) /
[plan](docs/plans/2026-06-16-options-gui-polish-plan.md)):
- **Nav dropdowns persist** across navigation — `webgui/main.py` stores each
  `ui.expansion` open/closed state in a module-level `_NAV_OPEN` dict (single-user,
  like `_CACHE`); first visit still auto-opens the active group.
- **Scanner**: signals colored by quality via `score_zone_color` (zones match the
  speedometer) on a `body-cell-composite_score` slot; the VIX term label is plain
  English via `term_text` ("VIX term: Contango (near-term calm) · as of 1:32 PM");
  newly-appeared signals get a **NEW** badge via a session diff (`mark_new`,
  page-side, resets on reload — both 0-DTE + Swing tables).
- **Paper Trades**: the detail panel now re-renders for the selected row on each
  data refresh (`paper.py` tracks `sel_id`, re-calls `detail_panel.update`).
- **Captured Signals**: drift shown as `x.xx` (numeric value kept for sort, a
  `body-cell-score_drift` slot renders `toFixed(2)`); rows colored by
  recommendation (`rec_color`: HOLD amber / TAKE_PROFIT green / CUT red).
- **Calculator**: P&L grid range is symmetric about spot and widened to span the
  strikes — `compute.symmetric_price_range` in `calc_compute` (engine untouched).
- **Symbol inputs auto-select on focus** (calculator/gamma/simulator/swing) via the
  shared `webgui/pages/options/inputs.py` `select_all_on_focus` helper.
- **Gamma status bar**: `compute.gex_status_view` (collector status via
  `gex_status.classify_collector_status` + `gex_history_db.last_snapshot_age`, plus
  last/next 5-min scan within 08:30–15:20 CT, reusing the scheduler's `_GEX_*`
  constants) is published each 30 s tick by `handlers.publish_gex_status`
  (`cache:options:gex_status`); `gamma.py` shows **Collector / Last scan / Next scan**
  alongside the existing "Next refresh" countdown.
- Pure transforms are unit-tested (webgui + options_svc suites).

> **Paper auto-manage (DONE — supersedes the old "manual-only" TODO).** The
> `options_svc` scheduler now reprices + auto-closes paper positions on its own:
> `manage_due` fires `run_manage_and_refresh` every **5 min** within market hours
> (`scheduler.py:97,104,219`), so the Paper Portfolio updates unattended. The
> "Run Manage Cycle" button is now a manual trigger of the same cycle, not the only
> path. (Tick cadence reference: each 30 s scheduler tick also runs
> `refresh_header` + `publish_gex_status`; the 2-min GEX collect and 5-min manage
> are slot-gated within 08:30–15:20 CT.)

**Gamma panels / walls / flicker batch (DONE — 2026-06-16).** Four fixes from a
live-screenshot review (design/plan:
[design](docs/plans/2026-06-16-gamma-panels-walls-flicker-design.md) /
[plan](docs/plans/2026-06-16-gamma-panels-walls-flicker-plan.md)):
- **Proportional panels**: `gamma.panel_flex(n_cols)` sets the bar/heatmap column
  flex ratio from the intraday snapshot count (heat fraction lerps 0.28→0.70 over
  ~82 five-min slots), so the heatmap expands and the bars shrink as the session
  fills in. Term view → bars full width, heatmap hidden.
- **GAMMA dead space**: `gamma.significant_strikes(bars, frac=0.03)` feeds the
  shared y-range from strikes with |net| ≥ 3 % of peak, cropping GEX's near-zero
  edge strikes (other views were already tight). Both panels share the range.
- **Flicker**: the two Highcharts elements are created **once** and updated in
  place (`el.options = …; el.update()`); `_render_view` no longer
  `clear()`s/rebuilds the canvas. Message labels toggle via `set_visibility`.
  (Now `ui.highchart`; the bar↔Term kind switch recreates via `_set_chart`.)
- **Single walls**: `services/options_svc/compute.gamma_walls` returns one Put +
  one Call wall via the engine's `get_directional_walls` (call = max-call-GEX
  strike above spot, put = most-negative-put-GEX below) instead of the old
  `get_gex_walls`/`get_dex_walls` top-5; the page renders them unchanged. DEX
  per-strike map remapped `dex`→`gex` for the picker.

**Trade page (`/trade`) — DONE (2026-06-16, born 3-tier — Phase 4).** The Trade
Analyzer was built directly on the 3-tier model (no in-process stage). New
service `services/trade_svc` (:8213, `SERVICE_PORTS["trade"]`), **on-demand only
(no scheduler)** — the page enqueues an `analyze` command on `cmd:trade`; the
service computes and writes `cache:trade:analysis` (one latest-result view, like
sim/calc) + publishes `events:trade:analysis`; the page version-polls and
repaints (persists across nav). Pieces:
- **Contract** `shared/contracts/trade.py:TradeAnalysis` — validates the analyze
  envelope (symbol + verdict/momentum/sector sub-dicts) before caching.
- **`trade_svc/compute.analyze(symbol)`** ports the legacy desktop
  `trade_analyzer.py` `analyze()` flow (the un-copied orchestration): fetch MTF
  data via the proxy (`_proxy.schwab_client`; 1/5/15/60-min + daily, SPY +
  sector-ETF daily), compute indicators **reusing `shared/analysis_lib/technical`**
  (`calculate_ema_alignment`/`calculate_rsi`/`calculate_adx`/`calculate_macd`/
  `calculate_vwap`/`calculate_relative_volume`/`calculate_volume_profile`), build
  `PositionInputs`/`InvestorInputs`, and score the copied
  `trade-analyzer/src/analysis/recommendation` verdict engines. **Defensive**
  (degrades to an `errors` payload, never raises). `technical` is imported
  **standalone** (its dir on `sys.path`) to dodge the `shared.analysis_lib`
  package `__init__` (which eagerly imports a broken `schwab_client`); safe
  because the service is its own process (same isolation `sentiment_svc` uses for
  `scoring`). Symbol→sector via a built-in large-cap map (`_SYMBOL_SECTOR`) with a
  **neutral** SectorStrength fallback when unknown.
- **`trade_svc/handlers.analyze`** runs compute → `TradeAnalysis` gate → cache +
  publish; `handle_command` dispatches `analyze`. **`trade_svc/app.py`** =
  `make_app("trade", command_handler=…)` (no scheduler).
- **Page** `webgui/pages/trade.py`: symbol input (+Enter) → Analyze; renders a
  header (symbol/price/bias/vol), two verdict cards (verdict colored BUY-green/
  HOLD-amber/SELL-red, score, top reasons, ⛔ hard gates, expandable factor
  breakdown table), MTF-alignment card, momentum strip, sector card. Pure builders
  (`verdict_color`/`bias_color`/`momentum_rows`/`breakdown_rows`/`alignment_rows`)
  unit-tested in `webgui/tests/test_trade.py`.
- **Fundamentals wired via the proxy (2026-06-16).** `compute.analyze` fetches
  Schwab fundamentals through a new proxy endpoint
  `GET /instruments?symbol=X&projection=fundamental`
  (`SchwabProxyClient.get_fundamentals` → unwraps `instruments[0].fundamental`)
  and parses them with `parse_schwab_fundamentals`. `InvestorVerdict` now runs on
  real data and `fundamentals_available` = `Fundamentals.is_sufficient()`; the
  page shows a **Fundamentals card** (P/E, PEG, rev/EPS growth, ROE, margin
  trend) when available, else the insufficient-data note. **Parser is a superset**
  (`trade-analyzer/src/analysis/fundamentals.py`): the *real* Schwab fields
  (`revChangeTTM`/`epsChangePercentTTM` in percent→fraction, `returnOnEquity` as
  percent via a `>2` magnitude heuristic, `operatingMarginTTM` vs `MRQ` for the
  margin trend) are primary, the legacy speculative names are fallback (all old
  tests stay green). The instruments payload has **no** next-earnings date / EPS
  surprises / guidance / FCF, so those degrade to None (the Position earnings gate
  never fires; `days_to_earnings` is None). Fetch is defensive — a proxy/parse
  failure degrades to insufficient-data HOLD, never raises.
- Tests: `services/trade_svc/tests` (compute/handler/app) + `webgui/tests/test_trade.py`.
  Design/plan: Phase 4 of the [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**Validated swing evaluation (Trade page) — DONE (2026-06-28).** The `/trade`
**Position** verdict's hand-weighted, never-validated swing scoring is replaced by a
**backtested, IC-weighted cross-sectional factor model** whose weights are learned from
forward returns. Investing (months+) is **deferred** — it can't be backtested without a
point-in-time fundamentals source. Honest framing: the model is *validated* (it shows a
small **positive out-of-sample IC** + a calibrated quintile spread), not *guaranteed* —
the edge is thin and regime-dependent. Architecture = **offline fit → versioned artifact
→ online score** (the C-ready shape from the design: a single regime key `"all"` today;
`"trend"/"chop"/"highvol"` drop into the same loader/scorer later). Pieces:
- **PURE factor library** `trade-analyzer/src/analysis/factors.py` — each factor is
  `(daily_df) → pd.Series` over a daily-OHLCV frame, **sign-corrected so higher =
  bullish**, **causal** (the value at bar *t* uses only data ≤ *t* — no look-ahead).
  Winsorization/standardization are NOT per-factor; they happen **cross-sectionally at
  scoring** (`zscore_by_date`, across symbols per date → no temporal leakage). The live
  value is the Series' last element, so the SAME code feeds the backtest and the live
  scorer (no drift). The 10 registered factors: **mom_12_1** (12-1 intermediate
  momentum, skip-month), **mom_6_1**, **pth** (price ÷ 252-day high — George & Hwang
  anchoring), **str_5d** (short-term 5-day reversal, sign-corrected), **vol_adj_mom**
  (3-mo return ÷ realized vol), **trend_quality** (distance above the 50/200-EMA stack),
  **low_vol** (−60-day realized vol), **rs_spy** (63-day excess return vs SPY),
  **rs_sector** (63-day excess vs the sector ETF), **turnover** (volume ÷ 63-day avg —
  the conditioning var). The `FACTORS` registry is the single source of truth; the
  **harness's IC decides which earn weight** (not the hand-picked list).
- **OFFLINE harness** `trade-analyzer/src/analysis/backtest.py` (pure — operates on a
  `(date,symbol)`-MultiIndex panel + forward Series, no I/O): `factor_ic`
  (per-date cross-sectional **Spearman rank IC** → mean_ic/icir/n_days; ICIR only
  trusted with ≥5 IC-days + real dispersion), `quantile_spread` (top-minus-bottom),
  `zscore_by_date` (cross-sectional winsorize @ 2/98 + standardize, look-ahead-free),
  **`signed_ic_weights`** (the production weighter: `weight_k = mean_ic_k / Σ|mean_ic|`,
  **keeping the sign**, above an n-independent noise floor — so a wrong-sign-but-
  predictive factor like low_vol carries a **NEGATIVE** weight and contributes with the
  correct sign; chosen over ICIR-/t-stat-weighting because those are n-dependent and
  unstable across small per-fold samples), `composite`, **`walk_forward`**
  (rolling train→test, weights fit per train window, composite OOS IC on the unseen test
  window; train/test never overlap), `calibrate` (bucket composite into quantile bands →
  per-band score range + mean forward + hit-rate `P(fwd>0)`, **isotonic-smoothed** so a
  higher-ranked band never shows a lower stat). The orchestrator
  **`trade-analyzer/fit_swing_model.py`** (run manually/periodically, **NEVER imported by
  a service**) pulls ~78 liquid symbols' (curated `UNIVERSE_SECTOR` → sector ETF) **5-yr**
  daily history via the proxy (concurrent), builds the panel with **20-day forward
  EXCESS-return-vs-SPY** labels (the prediction target — factors are causal so the future
  H-bar label is legitimate), runs the engine (train/test/step **378/63/63**), and writes
  the artifact + a markdown research report.
- **Artifact** `trade-analyzer/data/swing_model.json` (gitignored under `data/`; path
  `repo_paths.SWING_MODEL`, report `SWING_MODEL_REPORT`) — `version` (the fit date),
  `fit_universe_n`, `horizon`, and per regime: signed **`weights`**, **`factor_ic`**
  (mean_ic/icir/n_days per factor), the cross-sectional **`norm`** (per-factor
  time-averaged winsorized cross-sectional mean/std — the basis the calibration was built
  on), the score→outcome **`calibration`** (5 quantile bands → score range / mean_fwd /
  hit_rate / n), and **`oos_ic`** + `oos_ic_by_fold` + `n_folds`.
- **LIVE scorer** `services/trade_svc/swing_model.py` (on-demand, defensive → returns
  `None` so `analyze()` falls back to the legacy verdict on ANY failure): loads the
  artifact, z-scores the symbol's current factors on the artifact's **cross-sectional
  norm** (PRIMARY — calibration-consistent regardless of the live cross-section size; the
  cached universe snapshot is a noisy SECONDARY fallback), **clips z to ±3** (`Z_CLIP` —
  matches the fit's per-date 2/98 winsorization; stops a live outlier like a turnover
  spike hijacking the signed composite), `composite = Σ signed_weight × z`, then reads the
  **calibration band** containing the composite → **BUY** (top band) / **SELL** (bottom) /
  **HOLD**, a band-quantile **percentile**, the band's expected forward return + beat-SPY
  hit-rate, and per-factor contributions (z · weight · contribution · historical IC).
  `analyze()` fetches **2-yr daily** so every long-warmup factor (mom_12_1 needs 273 bars;
  pth/low_vol roll 252) populates at the last bar.
- **Contract / cache:** additive optional **`swing_model`** block on `TradeAnalysis`
  (→ `cache:trade:analysis`); `compute.get_universe_snapshot()` lazily rebuilds a daily
  **`cache:trade:universe_factors`** snapshot ({factor: [values across the curated
  universe]}) as the defensive cross-sectional fallback basis.
- **UI** `webgui/pages/trade.py` (Position card): the validated swing verdict is the
  **headline** + a calibrated outcome line (e.g. `90th pctile · +1.3% excess / 20d · 52%
  beat-SPY` via `swing_headline`), a **"Why — validated factors"** expander (per-factor z
  / weight / contribution / historical IC + the model version & OOS IC via
  `swing_contrib_rows`/`swing_model_meta`), and the **legacy heuristic** verdict tucked
  into a collapsed **"Legacy heuristic"** expander (`_legacy_verdict_body`). Falls back to
  the legacy body verbatim when `swing_model` is absent. Investor + Markov cards
  unchanged — **the Markov card still forecasts the legacy technical-momentum
  `composite_daily`**, NOT the validated composite (a separate lens; a documented
  coexistence, not a bug).
- **Validated result (current fit, `version` 2026-06-28):** fit universe **78** symbols,
  horizon **20d**, **13** walk-forward folds. Composite **OOS IC ≈ +0.0367** — but **5 of
  13 folds are NEGATIVE**, so the edge is thin and **regime-dependent**. Calibration: top
  quintile (band 4) ≈ **+1.35% / 4 wk at 52.3% beat-SPY**, bottom (band 0) ≈ **−0.80% /
  43.3%**. Signed weights (the ONLY factors that cleared the |IC| floor): **low_vol
  −0.34** (reclaimed with a NEGATIVE weight — high-vol names outperformed in this 5-yr
  large-cap bull period, IC −0.066), **mom_12_1 +0.21**, **mom_6_1 +0.17**,
  **trend_quality +0.12**, **rs_sector +0.08**, **turnover +0.07** (pth / str_5d /
  vol_adj_mom / rs_spy fell below the floor → weight 0).
- **Honest caveats (state these):** the edge is small + regime-dependent; it leans on
  **low_vol's inverted sign** reflecting this bull-ish large-cap period (it could flip);
  **survivorship bias** (the fit universe is today's liquid survivors) + **regime
  non-stationarity** (a 5-yr fit may not hold forward); the LIVE cross-section
  (~watchlist) is thin vs the fit universe. Validation reduces self-deception, it does not
  guarantee forward performance — **re-run `fit_swing_model.py` periodically**.
  **Regime-conditional weighting (Option C)** is the planned next step (same harness, new
  regime keys); ML (B) is gated on universe expansion.
- Tests: factor library + harness + live scorer + contract + page builders are unit-
  tested (TDD by layer, the design's acceptance gate = positive OOS IC + a meaningful
  spread on real data). Design/plan:
  [design](docs/plans/2026-06-22-swing-validated-evaluation-design.md) /
  [plan](docs/plans/2026-06-22-swing-validated-evaluation.md).

**Markov 2.0 (Trade page) — DONE (2026-06-21).** A probabilistic, forward-looking
layer on the `PositionVerdict`: model the composite score as a 5-state Markov chain,
surface where it's heading, and apply a bounded tilt to the score (the BUY/HOLD/SELL
label is untouched). Pieces:
- **States = composite-score bands** anchored at the decision boundaries (S1
  Strong-Bear `[-100,-40)` = SELL · S2 Weak-Bear `[-40,-15)` · S3 Neutral `[-15,15)`
  · S4 Weak-Bull `[15,40)` · S5 Strong-Bull `[40,100]` = BUY), so a forecast reads
  directly as P(cross into BUY/SELL).
- **PURE engine** `trade-analyzer/src/analysis/markov.py` (no I/O): `classify_band`,
  `count_matrix` (NaN/None breaks the chain), `pooled_prior`, `shrink`
  (Dirichlet-multinomial, α≈30), `project` (`dist·P^n`), `forecast`
  (P(BUY)/P(SELL)/E[score]/persistence/**stationary via power-iteration** — robust to
  reducible chains), `row_confidence`, `drift_tilt` (clamped ±12, confidence-weighted).
- **Daily score reconstruction** `trade_svc/compute.reconstruct_daily_composite(daily,
  spy, sector_hist)` builds a parallel **"Markov base score" (`composite_daily`)** from
  ONLY daily-reconstructable factors (the live verdict's intraday VWAP/rel-vol/MTF-EMA
  can't be rebuilt for past bars), renormalized to 100, fully vectorized; a missing-close
  bar → NaN (no observation). The chain runs on `composite_daily`, which also **dissolves
  the feedback loop** — the tilt is added to the displayed `composite_full`, never to
  `composite_daily`, so it can't feed back into the matrix.
- **Hybrid matrix:** per-symbol day-to-day counts `shrink`-blended toward a pooled prior
  built across a curated 17-symbol universe (`build_pooled_prior` / `get_prior`, cached at
  `cache:trade:markov_prior`, lazy daily refresh, uniform fallback on failure).
- **Wiring:** `build_markov_block` runs **defensively** inside `compute.analyze()`
  (any failure → `markov: None`, verdict unchanged) and rides an additive optional
  `markov` block on the `TradeAnalysis` contract → `cache:trade:analysis`.
- **Page** `webgui/pages/trade.py`: the Markov Forecast card sits in the **verdict row as
  the third equal-width card** alongside **Position · 1–8 wk** and **Investor · months+**
  (all `flex-1 min-w-[280px]`, `items-stretch` — three equal frames in one row, wrapping on
  narrow screens). The row and its three cards are **persistent** and the two verdict cards
  are **refilled in place** (`_fill_verdict_card`) so the Markov card's Highcharts element
  is never destroyed by a `clear()` (it's built once per the ESM-import-map gotcha, with an
  explicit `chart.height` + reflow-on-show, updated in place). The card holds a band chip, a
  stacked-area band-probability-over-horizon chart (now/5/10/20d), per-horizon
  P(BUY)/P(SELL)/E[score], and a drift/tilt/persistence line; the Position card headline
  shows the `markov_adjusted_score` (with a `base … · Markov …` subtitle) — **the
  BUY/HOLD/SELL label is unchanged** (the tilt is advisory on the score). When `markov` is
  absent the card hides and the row falls back to Position + Investor (two equal cards). Pure
  builders (`markov_band_chip`/`markov_metric_rows`/`markov_drift_row`/
  `markov_forecast_figure`/`position_headline`) unit-tested.
- Design/plan: [design](docs/plans/2026-06-21-markov-trade-analyzer-design.md) /
  [plan](docs/plans/2026-06-21-markov-trade-analyzer.md).

**Autonomous Driver — Claude decision layer (`/driver`) — DONE (2026-06-24).**
The driver's hardcoded `trade_selector` rule tree is replaced by a strategy-agnostic
**Claude decision layer** (autonomy **level B** — autonomous **paper** execution, NO
approval gate) that pursues **net $500/day** by selecting + sizing **defined-risk
option credit spreads (PCS/CCS/IC)** from the scanner. *Honest framing:* it **targets**
$500/day (presses when edge exists, **stands down** when it doesn't, **banks** the day
at +$500, hard-capped on the downside) — no decision-maker can guarantee it. Pieces
(all `services/driver_svc`):
- **PURE safety core** `guardrails.py` (the load-bearing module — the model PROPOSES,
  this code DECIDES, it never trusts the model with risk): `is_allowed` (defined-risk
  allowlist PCS/CCS/IC, structure read from `structure`→`type`→`trade_type` so the RAW
  scanner signal classifies correctly — real signals store it in `type`),
  `clamp_quantity` (resize to `min(request, per-trade cap, daily-budget cap)`, floored;
  0 on unaffordable / NaN / inf), `halt_state` (banked-$500 → daily-loss-cap → VIX>25),
  `apply_guardrails` (halt → stand-down → per-trade resolve-from-menu / allowlist /
  clamp / max-trades+concurrent, tracking the remaining budget across trades).
  Exhaustively unit-tested.
- **Decider** `decider.py`: `build_packet`'s model-facing prompt + a forced
  `submit_decision` tool-use call to **Claude Opus 4.8** (`anthropic` SDK, LAZY import,
  key via `api_keys.anthropic_api_key()` — env / gitignored `shared/anthropic_key.txt`);
  `parse_decision` is total over adversarial JSON and **every failure → stand-down**
  (never raises, never trades blind).
- **Compute**: `build_packet` (top-N composite-scored menu + day-P&L gap-to-target +
  `menu_by_id`→raw signal; day-P&L from the paper snapshot's `session_pnl`; real
  scanner keys `type`/`expiration`/`pop_pct`) + `run_cycle` (`build_packet → decide →
  apply_guardrails`, never raises) + `fetch_market_context`.
- **Handlers**: `run_autonomous_cycle` (gate on control → run_cycle → enqueue
  `cmd:options` `paper_create` per survivor [a `source="driver"` COPY + the CLAMPED
  qty; each enqueue isolated so a mid-loop failure can't skip the latch/publish] →
  latch the kill-switch on halt → publish `AutonomousState`); control read/write;
  `cycle`/`enable`/`disable`/`stop` commands.
- **Scheduler**: `checkpoint_due` (09:30–16:00 ET, 30-min slots) + `should_rearm`
  (next-day halt clear) wired into the loop on the executor, each branch guarded; the
  legacy 09:28 `morning_due`→`run_morning` path coexists (autonomy is gated OFF by
  default so they don't conflict).
- **Contracts** `DriverControl` (`cache:driver:control` — master switch + STOP latch)
  + `AutonomousState` (`cache:driver:autonomous` — the monitor view). Tunables in
  `settings.py` (`DAILY_TARGET=500`, `PER_TRADE_MAX_RISK=300`, `DAILY_RISK_BUDGET=900`,
  `MAX_CONCURRENT=6`, `MAX_TRADES_PER_CYCLE=3`, `MODEL="claude-opus-4-8"` (build
  default; the **`DRIVER_MODEL`** env var overrides it per-deployment —
  e.g. `claude-sonnet-4-6` for lower cost), `CHECKPOINT_MIN=30`); the daily loss
  cap is sourced from the legacy
  `config.RISK_LIMITS`.
- **Page** `webgui/pages/driver.py`: a Tier-1 **monitor + override** (Enable/Disable,
  confirm-gated **STOP**, **Run now**, $500 progress, open-driver-positions, newest-
  first decision-log audit) reading `cache:driver:autonomous`/`control` + version-
  polling; engine-free (3-tier rule). The legacy approval queue + Performance UI stays.
**Real `/ES` `/MES` FOP shelved** — Schwab can't serve FOP chains or place futures/FOP
orders (equity+option only); see [[schwab-api-instrument-limits]]. v1 executable
universe is scoped to scanner spreads (equities + Claude-managed exits + live/level-C
are v2 — see the design doc). **Master switch defaults OFF** — set `ANTHROPIC_API_KEY`
and Enable on `/driver` to run it; with no key it safely stands down. driver_svc **130**
+ contracts **34** + webgui **483** green (incl. a Redis-driven e2e proving qty=3
clamps to 1 through the real pipeline; built subagent-by-subagent w/ per-unit TDD +
spec/quality review). Design/plan:
[design](docs/plans/2026-06-24-driver-autonomous-claude-decider-design.md) /
[plan](docs/plans/2026-06-24-driver-autonomous-claude-decider-plan.md).

**Driver isolated paper account + performance scorecard (`/driver`) — DONE
(2026-06-25).** The autonomous Driver now trades into — and grades itself against — its
**own dedicated paper book**, isolated from the user's manual paper account, so its real
performance is measurable. This also fixed a latent **write/read split**: the Driver
*wrote* `paper_create` into the flat LEDGER (`trades.db` — no repricing/auto-manage, so
its trades were inert rows and its `source="driver"` tag was dropped) but *read* its
day-P&L/$500-target/halt from the user's ENGINE account (`paper_account.db`) — measuring
the wrong book and never repricing its own trades. Pieces:
- **Dedicated account.** `repo_paths.DRIVER_PAPER_DB =
  options-scanner/data/paper_account_driver.db` ($25k start). Every `paper_account_db`/
  `paper_engine` fn already takes a `db_path`, so a second DB file is a fully independent
  single-account store — **zero schema change** (the `CHECK(id=1)` single-account
  constraint is sidestepped by using a separate file).
- **`services/options_svc` (owns ALL `paper_engine` imports):**
  `compute.open_driver_position(signal, qty)` (extracted from `run_entry_cycle`'s
  per-signal block — simulated fill → re-size on the ACTUAL fill credit → reserve BP →
  `paper_engine._record_order` (preserves the `entry_order_id` link) → `insert_position`;
  the guardrail qty is a **CEILING**, `open_qty = min(clamped, sized-on-fill)`; never
  raises); `run_driver_manage_cycle()` (`paper_engine.run_manage_cycle(db_path=
  DRIVER_PAPER_DB)` — reprice + auto-exit + session roll + halt, try/except never-raise);
  `driver_account_view()` / `driver_account_perf()`; the PURE
  **`driver_perf.build_scorecard(positions, snapshot)`** (# trades, open/closed, **win
  rate**, **profit factor** [None when no losses yet → render "—"], avg win/loss,
  realized/unrealized/total P&L, best/worst [drawn from the None-pnl-excluded set],
  **P&L by symbol & by strategy**). Handlers: `refresh_driver_paper` publishes **both**
  views (**NO rescue overlay** — that reads the manual book) + `run_driver_manage_and_refresh`;
  commands `driver_paper_create` / `driver_paper_manage` / `driver_paper_reset`.
  Scheduler: the 5-min `manage_due` slot reprices the driver account in its **OWN guarded
  branch** so a driver-side failure can't skip the manual refresh.
- **`services/driver_svc` (engine-free re the paper account — only enqueues + reads
  cache; it must NOT import `paper_engine`/`paper_account_db`, which transitively pull
  `scoring`/`signal_repricer` → the documented cross-app module collision):**
  `run_autonomous_cycle` enqueues **`driver_paper_create`** (not `paper_create`), reads
  day-P&L + open positions from `cache:options:driver_paper_account`
  (`CACHE_OPT_DRIVER_PAPER`), and attaches the scorecard (`cache:options:driver_paper_perf`)
  to the published **`AutonomousState.perf`** (new additive field). `build_packet`
  open-position attribution is correct-by-construction (the whole driver DB is the
  driver's — the dead `source=="driver"` filter falls back to the full account).
- **Cache views:** `cache:options:driver_paper_account` (snapshot + open positions) +
  `cache:options:driver_paper_perf` (the scorecard) — published on each
  `driver_paper_create` and every 5-min manage tick.
- **Page** `webgui/pages/driver.py`: the monitor's Day-P&L bar / summary / open positions
  **re-point** to `cache:options:driver_paper_account` (was the manual `paper_account`); a
  new **Performance scorecard card** (pure builders `scorecard_headline_chips` /
  `scorecard_quality_chips` / `scorecard_symbol_rows` / `scorecard_strategy_rows` /
  `best_worst_text`) renders `cache:options:driver_paper_perf` directly (live — refreshes
  on the 5-min tick, not just the 30-min cycle). Engine-free (3-tier rule).
- **PAPER ONLY** — `config.PAPER_TRADE` stays True; the driver never flips it. The
  historical ledger MU trades are left where they are (the driver starts fresh in its
  dedicated account). options_svc **285** + driver_svc **138** + contracts **35** +
  webgui **510** green (incl. a Redis-driven e2e proving `driver_paper_create` lands ONLY
  in the driver DB — manual account untouched — and both views + the scorecard reflect it,
  with a non-vacuity leak check). Built subagent-by-subagent (TDD, two-stage spec+quality
  review per unit). Branch `Using_Highcharts`. Design/plan:
  [design](docs/plans/2026-06-25-driver-isolated-paper-account-design.md) /
  [plan](docs/plans/2026-06-25-driver-isolated-paper-account-plan.md).

**Driver page (`/driver`) — DONE (2026-06-16, born 3-tier — Phase 5).** The
order-approval queue was built directly on the 3-tier model. New service
`services/driver_svc` (:8214, `SERVICE_PORTS["driver"]`), **scheduled (09:28 ET)
+ command-driven**. The order-approval queue is a Redis Streams flow: the morning
pipeline produces a *pending* approval cached at `cache:driver:approvals`; the
GUI APPROVE/SKIP buttons enqueue `cmd:driver` commands the consumer acts on.
Pieces:
- **Contracts** `shared/contracts/driver.py`: `ApprovalState` (the
  pending/decided morning payload — grade, grade_reasons, conditions, pnl, a
  loose `proposed_trades: list[dict]`, status pending/no_trade/error/approved/
  skipped, decision, results, reasons, error) + `PerfReport` (summary + trades).
  Validate the envelope shape before caching, like `ScanResult`/`TradeAnalysis`.
- **`driver_svc/compute`** ports the legacy `morning_agent.run_morning_agent()`
  orchestration **minus** its side effects (no `pending_trade.json` write, no
  HTTP post to the :8300 approval server): `run_morning()` calls the SAME
  building blocks (`check_service_health`/`fetch_all_ml_signals`/
  `fetch_gex_snapshot`/`fetch_market_conditions`/`fetch_current_pnl`/`grade_day`
  → `trade_selector.select_trades`) and **returns** the payload; `execute()` →
  `order_executor.execute_trades` (**`config.PAPER_TRADE=True` → simulated**, not
  modified); `build_perf_report()` → `perf_report.build_report`. All **defensive**
  (degrade to an `error`/empty payload, never raise). claude-driver engines are
  imported **standalone** (its dir on `sys.path`) — safe because the service is
  its own process (same isolation `sentiment_svc`/`trade_svc` use; note: importing
  both `driver_svc` and `trade_svc` engines in **one** process re-triggers the
  documented `config` module-name collision, so run service test suites **per
  folder**, never `pytest services` over all of them).
- **`driver_svc/handlers`**: `run`→cache pending approval; `approve`→**only if
  still pending**, `execute` the proposed trades + re-cache as `approved` w/
  results; `skip`→mark skipped; `perf`→cache `cache:driver:performance`. Each
  validates + caches + publishes an event. **`driver_svc/scheduler`**:
  `morning_due(now, last_run_date)` fires `run_morning` once/day at/after 09:28 ET
  on weekdays (holiday short-circuit lives in `compute.run_morning`) + keeps the
  perf view warm; **the scheduler NEVER executes orders** (only an explicit
  `approve` does). **`driver_svc/app`** = `make_app("driver", scheduler=loop,
  command_handler=…)`.
- **Page** `webgui/pages/driver.py`: Run-morning-agent + Refresh-performance
  buttons; an approval card (grade chip, conditions strip, grade rationale,
  per-bucket proposed-trade cards) with **APPROVE (confirm dialog)** / **SKIP**
  when pending, else a decision banner (approved/skipped/no_trade/error); a
  Performance section (summary line + trade table). Version-polls
  `driver:approvals` + `driver:performance`; persists across nav. Pure builders
  (`grade_color`/`status_text`/`condition_rows`/`proposed_trade_lines`/`perf_*`)
  unit-tested in `webgui/tests/test_driver.py`.
- Tests: `services/driver_svc/tests` (compute/handlers/scheduler/app) +
  `webgui/tests/test_driver.py`. Design/plan: Phase 5 of the
  [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**Portfolio page (`/portfolio`) — DONE (2026-06-16, born 3-tier — Phase 3).** The
stub Portfolio page was built directly on the 3-tier model. New service
`services/portfolio_svc` (:8212, `SERVICE_PORTS["portfolio"]`), **scheduled +
command-driven** — uniquely it keeps a **live model in memory** that a background
SSE consumer updates tick-by-tick. Pieces:
- **Contract** `shared/contracts/portfolio.py:PortfolioModel` (one view,
  `cache:portfolio:positions`): display-ready `holdings_rows` / `sector_rows` /
  `performance_rows` (already formatted by the engine `view_model` in Tier 2),
  the per-symbol `suggestions` map, and `proxy_up`/`streaming` meta. Validates the
  envelope shape before caching, like the other domain contracts.
- **`portfolio_svc/compute`** reuses `portfolio-analyzer/src` **verbatim**:
  `build_portfolio` (sector breakdown + the four comparisons: vs-sector RS,
  benchmark over/under-weight, since-purchase excess, tailwind), `compute_baseline`
  (slow per-EQUITY history stats — ports the desktop `_compute_baselines` worker),
  `evaluate_portfolio` + `suggest` (the live scorecard + advisory rules), and the
  app's `view_model` formatters. **Formatting lives in Tier 2** (`format_payload`)
  so the GUI stays a thin renderer. `src` is imported **standalone** (PORTFOLIO_ANALYZER
  on `sys.path`) — safe because the service is its own process (note: `portfolio-analyzer`
  AND `trade-analyzer` BOTH expose a top-level `src` package, so they collide if
  imported in one process — another reason to run service suites **per folder**).
  All functions defensive (degrade, never raise).
- **`portfolio_svc/state`** holds the in-memory `PortfolioState` singleton (raw
  model + baselines + `dirty`/`rebuild_requested` flags + a lock) shared by the
  scheduler thread and the command handler.
- **`portfolio_svc/handlers`**: `rebuild` (proxy health → daily trade sync →
  `build_portfolio` → baselines into state → publish), `publish_current` (re-format
  the current state + cache+publish — called on each throttled tick), `handle_command`
  (`refresh` → sets `rebuild_requested` for the scheduler, which owns the stream
  restart). **`portfolio_svc/scheduler`**: builds the model, runs a background SSE
  worker (`compute.make_data().stream_quotes` → `apply_tick` to the shared model),
  and a 2 s publish loop that republishes when ticks are pending + does a full
  rebuild every ~10 min or on a pending refresh (restarting the stream on fresh
  holdings). Pure `rebuild_due`/`apply_tick_to_state` are unit-tested. **`portfolio_svc/app`**
  = `make_app("portfolio", scheduler=loop, command_handler=…)`.
- **Page** `webgui/pages/portfolio.py`: Refresh button + proxy/stream status bar;
  **Holdings / Sectors / Performance** tabs (`ui.table` rendering the cached display
  rows directly); the Performance tab adds a suggestion detail pane (row click →
  full advisory reasons). Version-polls `portfolio:positions` (live P&L via the
  service's per-tick republish); persists across nav. Pure builders
  (`proxy_status`/`stream_status`/`suggestion_text`/`status_line` + column defs)
  unit-tested in `webgui/tests/test_portfolio.py`. (Removed the now-orphaned
  `main.py:_stub` — Portfolio was the last stub page.)
- Tests: `services/portfolio_svc/tests` (compute/handlers/scheduler/app, 20) +
  `webgui/tests/test_portfolio.py` (8). Design/plan: Phase 3 of the
  [3-tier plan](docs/plans/2026-06-15-three-tier-architecture-plan.md).

**EOD Report redesign — DONE (2026-06-27).** The summary/detail were rebuilt around
**Daily / Weekly(WTD) / MTD performance, per book** (manual paper ledger + Driver
account, shown separately), plus **trade-type breakdowns** (by strategy / 0-DTE-Swing /
status) and **TOC + collapsible `<details>` navigation** (no JS — works in-app AND in
the exported standalone files). New pure builders in `webgui/pages/eod.py`:
`normalize_trades(raw, *, kind)` (one uniform `{symbol, strategy, trade_type, status,
entry_date, exit_date, realized_pnl, credit}` shape — the ledger keys `entry_time`/
`exit_time`/`entry_credit_total`/`trade_type`, the driver positions key `entry_ts`/
`exit_ts`/`entry_credit`+`quantity` and carry **no** `trade_type`); `period_buckets`
(realized/closed by **exit** date, opened/credit by **entry** date, week-to-date =
Monday→today, month-to-date = 1st→today); `breakdown_rows(trades, key)`;
`performance_table_html` / `breakdown_table_html` / `toc` / `details_section` /
`_book_now_line`. The summary keeps its activity tiles, adds a per-book performance
block; the detail adds the breakdowns + reuses the existing section builders inside
`<details>`. **One additive service change**: `compute.driver_account_view()` now also
returns `closed_positions` (the open-only view couldn't date-bucket the driver's closed
trades) — requires an `options_svc` restart + a republish (`driver_paper_manage`) to
appear. **Realized reads `$0`/`—` until trades close** — correct by design, not a bug
(both books currently have only open positions). webgui **539** + options_svc green;
verified live (summary perf tables, detail breakdowns PCS 19/CCS 9 + SWING 21/0-DTE 7,
`<details>` collapse, exported files). Design/plan:
[design](docs/plans/2026-06-27-eod-report-redesign-design.md) /
[plan](docs/plans/2026-06-27-eod-report-redesign-plan.md).

**EOD Report page (`/eod` + `/eod/detail`) — DONE (2026-06-18; redesigned 2026-06-27,
see above).** A **pure-webgui**
end-of-day report — no new service/port. It reads the caches the existing services
already publish (`options:scan` / `options:captured` / `options:paper_trades` /
`options:paper_account` + `driver:approvals` / `driver:performance`
+ `options:driver_paper_account` / `options:driver_paper_perf`) and rolls them
into a **Summary** and a **Detailed** report. Scope is
**Options activity + Driver** only (portfolio/sentiment intentionally excluded).
Built entirely in `webgui/pages/eod.py` — honors the 3-tier rule (webgui imports
only `nicegui` + `shared.bus` + `shared.contracts`). Pieces:
- **Single-source body.** Pure builders produce one HTML **fragment** + a scoped
  `EOD_CSS` string (mirrors the `gamma.py` Explain pattern — `ui.html` strips
  `<style>`, so CSS goes through `ui.add_css` in-app and is inlined into the file
  on export). `summary_fragment(snap, detail_href)` / `detail_fragment(snap)` +
  per-section builders (`captured_section` / `paper_section` / `scanner_section` /
  `driver_section`) are all **defensive** (missing/empty cache → a "No data" note,
  never raises) and unit-tested in `webgui/tests/test_eod.py` (16).
- **Generate + archive.** The **Generate** button calls `generate()` →
  `read_snapshot()` (snapshots the live caches) → `wrap_document(...)` wraps the
  same fragment+CSS into standalone `<html>` docs → `write_archive(...)` writes
  `summary.html` + `detail.html` into `webgui/data/eod/<CT-date>/` (gitignored, like
  the rest of `webgui/data/`; same date overwrites). The summary page lists past
  archived dates (`archive_dates`, newest first). The **in-file** summary→detail
  link is the relative `detail.html`; the **in-app** link is the route `/eod/detail`
  (the fragment takes the link target as a parameter — the only in-app/file diff).
- **File serving.** `main.py` adds `@app.get("/eod/file")` (mirrors
  `/options/explain`): returns an archived file as a raw `HTMLResponse` so its own
  `<style>` applies. The page's "Open summary/detail file" buttons + archive links
  open it in a new tab.
- **Wiring.** `("/eod", "EOD Report", "summarize")` in `FLAT_NAV`; `@ui.page("/eod")`
  → `eod.render()` and `@ui.page("/eod/detail")` → `eod.render_detail()` (both active
  `/eod` so the nav item highlights on detail too); `/eod` + `/eod/detail` added to
  `test_shell.py`. Design/plan:
  [design](docs/plans/2026-06-18-eod-report-design.md) /
  [plan](docs/plans/2026-06-18-eod-report-plan.md).

**Expected Move page (`/options/expected-move`) — DONE (2026-06-20).** A standalone
Tier-3 page that charts a symbol's recent price action plus a forward expected-move
cone for a given option strike/expiration. Reached via a **new-browser-tab handoff**
button on Scanner, Paper Trades, Captured Signals, and Calculator (or standalone from
the Options nav with manual symbol+expiry). Pieces:
- **Compute (Tier 2, `services/options_svc/compute.py`):** `compute_expected_move(symbol,
  expiry, legs, lookback="auto")` fetches a **DTE-aware** trailing-history window
  (`em_lookback_spec` → `_fetch_em_candles`: auto ≈ **3× DTE** trading days clamped to
  [20, 252], short DTE ≤2 → intraday 30-min; or a fixed `1mo`/`3mo`/`6mo`/`1y` override —
  replaces the old fixed `_EM_HISTORY_BARS=130`, partial bars skipped), the option chain,
  and spot (live quote else last close), then derives ATM IV for the expiry
  (`atm_iv_from_chain` — nearest-strike `volatility`, percent→decimal, exact-then-nearest
  fallback) and the cone (`em_cone`: one point/day, `width(t)=spot·atm_iv·√(t/365)`,
  anchored at spot). Fully defensive — returns a JSON-safe dict with `error` set on any
  failure (candles still drawn even when IV is unavailable). On-demand only.
- **Command/cache (`handlers.py`):** the `expected_move` command → `cache:options:expected_move`
  + `events:options:expected_move` (one latest-result view, like `calc_result`/`sim_result`).
- **Page (`webgui/pages/options/expected_move.py`):** engine-free reader — enqueues the
  command and version-polls the view. Pure builders `expected_move_figure` (Highcharts
  **candlestick** via `extras=["stock"]` + Upper/Lower EM dashed line series + datetime
  xAxis + x/y `crosshair.label` boxes) and `leg_lines` (yAxis plotLines: short solid /
  long dashed, put-red/call-blue) are unit-tested. One persistent `ui.highchart` built at
  render (ESM-import-map gotcha), updated in place; `@guard` on handlers. A **Look-back**
  dropdown (`em_lookback_options`: Auto≈3×DTE / 1mo / 3mo / 6mo / 1y) re-runs the last
  query with the chosen window; the active spec label shows in the status line.
  **No blank non-trading-day gaps:** the chart renders via `ui.highchart(...,
  type="stockChart")` so the x-axis is **ordinal** (`xAxis.ordinal:True`) — weekend/
  holiday gaps in the historical candles collapse automatically — and the forward
  cone (`em_cone(..., holidays=scheduler._HOLIDAYS, trading_days_only=True)`) omits
  weekend/holiday points so it lines up contiguously with the candles.
- **Handoff (`handoff.py`):** `signal_to_em_payload(signal)` normalizes a scanner/captured/
  paper signal dict → `{symbol, expiry, legs}` (per-type strikes via `_EM_LEG_FIELDS`);
  `send_to_expected_move` stashes (`_pending["expected_move"]`) + opens the page in a new
  tab. Scanner/Swing keep the shared 3-button `add_row_actions`; Paper/Captured use the
  Expected-Move-only `add_expected_move_action` (their rows map via `synth_from_trade`/
  `synth_from_captured`, which expose `strategy`→`type`); Calculator builds the payload
  from its `leg_inputs`. Design/plan:
  [design](docs/plans/2026-06-20-expected-move-page-design.md) /
  [plan](docs/plans/2026-06-20-expected-move-page.md).

**Simulator Replay tab (`/options/simulator`) — DONE (2026-06-20).** The third
legacy Tk simulator tab (`Replay`, alongside the already-migrated What-if /
IV-shock) was migrated to the 3-tier model. The in-process `ChainSnapshot` that
`sim_fetch` already stashes (and which carries `price_history`) is re-priced
along the underlying's recent path by the existing pure
`options_simulator.ReplayEngine`. Pieces:
- **Compute (Tier 2, `services/options_svc/compute.py`):** `sim_replay(symbol,
  expiry, kind, strike, direction, lookback="auto")` runs `ReplayEngine.full_trace`
  via `aggregate_position`, then ports the legacy window's **gap-compression /
  session** layout (overnight/weekend breaks collapsed onto a consecutive integer
  x-axis; `gaps`/`sessions`/`ticks`/`resolution`) into a **JSON-safe** dict
  (`x`/`prices`/`greeks{delta,gamma,theta,vega,rho}`/`timestamps`/`lookback`). The
  re-priced path is a **DTE-aware** window fetched here (`replay_lookback_spec` →
  `_fetch_replay_history` via the proxy: 0-DTE → 1-min/1d · ≤5 → 5-min/3d · ≤15 →
  5-min/5d · >15 → daily/~½×DTE; or a fixed override key), **NOT** the snapshot's
  fixed 2-day history — the expiry/DTE is only known at replay time. Defensive:
  `{}` on missing snapshot/contract, `{"error": …}` on IV≤0 / no price history.
  It is a **separate command/cache view** from `sim_run` (replay depends only on
  the contract selector + look-back, NOT the dt/mult sliders — so slider drags
  stay cheap).
- **Command/cache (`handlers.py`):** the `sim_replay` command →
  `cache:options:sim_replay` + `events:options:sim_replay`.
- **Page (`webgui/pages/options/simulator.py`):** a third **Replay** tab (now the
  default). Pure builder `replay_figure(trace, cursor)` draws ONE Highcharts
  element with **6 stacked yAxes** (Price + 5 Greeks) over the integer x-axis;
  session boundaries are dashed xAxis plotLines and the **scrub slider** is a
  client-side cursor plotLine (no command — same idiom as the ΔS overlay). The
  x-axis stays NUMERIC (dates in the tooltip / readout) to sidestep the datetime
  crosshair epoch-ms gotcha; the hover tooltip is capped at **2 decimals**
  (`tooltip.valueDecimals`). A **Look-back** dropdown (`lookback_options`: Auto-by-DTE
  / 1-min·1d / 5-min·3d / 5-min·5d / 15-min·10d / Daily·20d) overrides the DTE-driven
  window; the active spec label shows in the cursor readout. Built once at render
  (ESM import-map gotcha), updated in place; version-polls `options:sim_replay`;
  enqueues only on contract-selector / look-back changes. Pure builders unit-tested;
  verified live end-to-end (SPY → 62-bar 1-min trace → rendered 6-panel stack). Plans:
  [replay](docs/plans/2026-06-20-simulator-replay-tab-plan.md) /
  [DTE look-back](docs/plans/2026-06-20-dte-aware-lookback-plan.md).

**System Status page (`/status`) — DONE (2026-06-19).** A **pure-webgui** at-a-glance
health board (no new service/port). It honors the 3-tier import rule — `webgui/pages/status.py`
imports only `nicegui` + `bus_client`/`proxy` + `repo_paths`. Pieces:
- **Component sweep.** `component_targets()` enumerates the components from
  `repo_paths` (Memurai :6379, schwab-proxy :8100, **Schwab Authorization** (the
  proxy's OAuth token state), the five services :8210–8214 via `SERVICE_URLS`,
  webgui itself). `_probe_one` checks each by `kind`: **memurai** →
  `bus_client.ping()` (new helper: `bus()._r.ping()`, never raises); **proxy** →
  `proxy.health()`; **auth** → `auth_status(proxy.health())` reads `has_token`/
  `token_expired`/`refresh_token_expired` (access-token-expired is still "authorized"
  since the proxy auto-refreshes; only a missing token or **expired refresh token**
  is red); **service** → HTTP `GET /health` (the `make_app` scaffold's probe,
  `{"up": True}`); **self** → always up. `_sweep` fetches `proxy.health()` **once**
  and shares it with both the proxy + auth cards. The whole sweep runs off-thread
  via `nicegui.run.io_bound` (short 2.5 s per-probe timeout so a dead component fails
  fast). `overall_status` rolls the results into a green/red/grey banner naming any
  down components.
- **Schwab Authorization card (2026-06-19).** Surfaces OAuth token validity
  separately from "is the proxy process up". When the proxy is reachable the card
  shows an **Authorize** / **Re-authorize** button (`AUTH_URL = {PROXY_URL}/auth`)
  that opens the proxy's OAuth re-login page (`GET /auth`) in a new tab via
  `ui.navigate.to(..., new_tab=True)`; when the proxy is down the card is grey
  ("can't check"). The card is **not** restartable (`restart_spec` → None) — its
  action is the login link, not a process relaunch.
- **Data-freshness table.** Below the cards, per-domain rows read each representative
  cache view's version + ts (new `bus_client.read_meta(view)` → `(version, ts)`) and
  show `age_text` + a STALE flag for **scheduled** views older than 600 s (`is_stale`);
  **on-demand** views (trade/driver) are never flagged. This distinguishes "service
  answers /health" from "service is actively publishing".
- **Per-component Restart (2026-06-19).** Each **offline** component card grows a
  **Restart** button (proxy + the five services + Memurai; never the webgui — it
  can't restart itself, and its card is always "up"). `restart_spec(target)` maps a
  component to how it restarts — a **script** spec (proxy / service: free the port
  then launch the venv python on the entry script; services pass `wait_port=8100`
  so they wait for the proxy) or a **service** spec (Memurai → `Start-Service`).
  `restart_command(spec)` builds the argv: a script spec spawns its own console
  via `cmd /c start … cmd /k call tools\restart_one.bat <kill_port> <wait_port>
  <script>` (detached, live logs); `restart_one.bat` taskkills the port's LISTENING
  owner (clears a wedged process) then hands off to `wait_and_run.bat`. The page's
  click handler spawns it, toasts, and schedules a 7s re-sweep. Verified live: a
  Restart click on the proxy bound :8100 within ~1s and the card flipped to Online.
- **Wiring.** `("/status", "System Status", "monitor_heart")` in the **More** nav
  group; `@ui.page("/status")` → `status.render()`; `/status` added to
  `test_shell.py`. Auto-refresh `ui.timer(15s)` + manual Refresh button (with
  spinner + re-entrancy guard). Pure builders (`component_targets`/`status_word`/
  `status_color`/`status_icon`/`overall_status`/`age_text`/`is_stale`/`freshness_row`
  + `restart_spec`/`restart_command` + `auth_status`) unit-tested in
  `webgui/tests/test_status.py` (34); render + live restart + live auth-card
  verified by screenshot.

**Rescue tested trades (`/options/rescue`) — DONE (2026-06-21).** An advisory +
one-click-apply rescue feature for **tested credit spreads** (PCS/CCS/IC). Architecture
is **"Approach C (hybrid)"**: cheap **at-risk detection** rides the existing 5-min
manage cycle (tags paper-account rows + publishes a summary for a nav badge), while the
expensive **ranked candidate menu** is computed **on-demand** via a command, and **apply**
executes through new paper-engine primitives behind a stale-price guard. Pieces:
- **Commission source of truth** `config/commissions.toml` — Schwab standard rates
  (options **$0.65/contract per leg**, futures $2.25/side, index-exchange-fee passthrough);
  loaded by `services/options_svc/commission.py` (`commission_for`/`futures_commission`/
  `is_index_symbol`). **Rule: don't hard-code rates** — add them here.
- **Contracts** `shared/contracts/options.py`: new `RescueAdvisory` + `RescueCandidate`
  (+ `RescueLeg`/`RescueMark`) — validate the advisory envelope before caching.
- **PURE engine** `services/options_svc/rescue.py`: `assess_position_risk` (ok/watch/
  tested/critical + 0-100 **heat**, thresholds mirror the manage-cycle stops),
  `strategic_context` (dealer-gamma/regime/settlement notes+flags), **11 candidate
  builders** (close, partial_close, narrow, convert_ic, convert_butterfly, broken_wing
  [advisory], roll_down, roll_out, roll_down_out, inverted [advisory], futures_hedge
  [advisory]), `score_candidate` (max-loss-reduction-per-net-$ + delta + credit-vs-debit
  penalty + GEX/regime modifiers), and the `rescue_candidates` orchestrator (ranks +
  attaches context/warnings; **per-item construction** so one bad candidate can't sink
  the advisory).
- **Compute (Tier 2)** `services/options_svc/compute.py`: `compute_rescue(position_id)`
  (loads the position, reprices via `signal_repricer.reprice_swing`, fills underlying from
  the `gamma_snapshot` spot when the live quote is missing off-hours, pulls regime from the
  sentiment bridge, runs the engine, returns a contract-validated dict — fully defensive),
  `assess_open_positions()` (cheap stored-marks pass for the badge), `_make_leg_pricer(symbol)`
  (per-expiry chain-mid pricer).
- **Handlers** `services/options_svc/handlers.py`: the manage-cycle overlay merges
  `rescue_state`/`heat` onto the paper-account view; `publish_rescue_summary`; `rescue` +
  `rescue_apply` command handlers (`rescue_apply` refuses non-paper/captured ids and **never
  mutates on a stale re-price**). Cache keys: `cache:options:rescue:<position_id>` (one
  per-position advisory) + `cache:options:rescue_summary` (n_tested + n_critical for the badge).
- **Apply primitives** `options-scanner/paper_adjust.py` (NEW): `apply_close`/
  `apply_partial_close`/`apply_narrow`/`apply_convert_ic`/`apply_convert_butterfly`/
  `apply_roll`/`apply_inverted` mutate the paper DB inside the existing cash/buying-power
  mechanism (reconciling reserved BP to the new max-loss), write an audit row, and the
  `apply_adjustment` dispatcher re-prices the candidate legs and **aborts without mutation**
  if economics drifted > tolerance or the position isn't OPEN. `options-scanner/
  paper_account_db.py` grows a `position_adjustments` audit table + a `parent_position_id`
  column on `paper_positions` (linked rolls) + `insert_adjustment`/`list_adjustments`.
- **Page** `webgui/pages/options/rescue.py` (Tier-1, engine-free): `render()` + pure builders
  (`heat_color`/`at_risk_rows`/`candidate_card_rows`/`cash_text`/`summary_line`) — an at-risk
  table (paper+captured, heat-colored) → select a position → enqueues `rescue` → version-polls
  `cache:options:rescue:<id>` → ranked candidate cards (execute cards Apply→confirm→
  `rescue_apply`; advisory cards show "manual"). One persistent `ui.highchart` (ESM-import-map
  gotcha); `@guard` on handlers; degrades to a waiting-for-service placeholder.
- **Wiring** `webgui/main.py`: `("/options/rescue", "Rescue", "healing")` in the Options nav
  group + `@ui.page("/options/rescue")` route + a red count badge (key `/options/rescue`) fed
  from `cache:options:rescue_summary`, cleared on page open; `/options/rescue` in
  `test_shell.py`; a `webgui/page_help.py` guide entry.
- **At-risk row highlights:** the `rescue_state`/`heat` overlay lands on
  `cache:options:paper_account`, so the heat-colored at-risk row tint is wired on the **Paper
  Portfolio** page (`/options/portfolio`, `pages/options/portfolio.py` `rescue_highlight` +
  `body-cell-symbol` slot) — live there. The earlier-wired tints in `paper.py` (paper_trades
  ledger) + `captured.py` render different views that don't carry the overlay, so they stay
  **dormant no-ops** (kept defensively; captured is forward-compatible if signals are ever
  flagged). Primary at-risk surfaces (Rescue page table + nav badge) work regardless.
- **Captured CUT signals as advisory candidates (2026-06-22).** A captured signal whose
  `recommendation` is **CUT** (a money/delta/time loss stop — not `TARGET_HIT`) is now an
  **advisory** rescue candidate. `compute.reprice_captured` tags each signal row with
  `rescue_state`/`heat` via `assess_position_risk`, **escalating a CUT to at least `tested`**
  (heat floored ≥60) so it lands on the rescue board; `compute_rescue(position_id, source=
  "captured")` loads the signal via `signal_db.get_signal`, runs the engine, and **forces every
  candidate `apply_kind="advisory"`** (a captured signal has no executable paper position — the
  cards show the roll/convert/close mechanics + economics for *manual* placement, no Apply). The
  `rescue` command carries an optional `source` arg (paper|captured; paper id coerced to int,
  captured passes the string `signal_id`); `RescueAdvisory` gained `source` + a `position_id:
  int | str`. The page (`at_risk_rows` keyed by `signal_id`, row-select passes `source`) surfaces
  them. Apply safety is enforced at three layers (forced-advisory cards · `rescue_apply` refuses
  non-paper ids · the apply branch int-coerces). The nav **badge stays paper-only** (captured
  CUTs show on the board, not the badge). Design: [captured-signals](docs/plans/2026-06-22-rescue-captured-signals-design.md).
- Design/plan: [design](docs/plans/2026-06-21-rescue-tested-trades-design.md) /
  [plan](docs/plans/2026-06-21-rescue-tested-trades-plan.md).

## Paths and ports: `repo_paths.py` + `config/ports.toml`

`repo_paths.py` at the repo root is the single source of truth for cross-app
paths and ports. Each entrypoint prepends the repo root to `sys.path` and imports
the constants it needs:

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL, APPSETTINGS, TOKENS, NICEGUI_PORT  # etc.
```

`config/ports.toml`:

```toml
proxy = 8100
options_analytics = 8200
approval = 8300
dashboard_frontend = 5173
nicegui = 8500            # the NiceGUI app
memurai = 6379            # Redis backbone (Tier 3)

[ml_servers]              # external processes — not started by this repo
MES = 8000
MNQ = 8001
ES  = 8004
NQ  = 8005

[services]                # Tier-2 domain services (repo_paths → SERVICE_PORTS/SERVICE_URLS)
sentiment = 8210
options   = 8211
portfolio = 8212
trade     = 8213
driver    = 8214
```

**Rule: never hard-code `D:\` paths or port numbers in the apps.** Add them to
`repo_paths.py` / `config/ports.toml` and import them.

`config/commissions.toml` is the single source of truth for **commission rates**
(Schwab standard: options $0.65/contract per leg, futures $2.25/side, index-exchange-fee
passthrough), loaded by `services/options_svc/commission.py` (used by the Rescue
candidate menu). **Rule: don't hard-code commission rates** — add them here.

## Secrets

Live in `shared/` and are **all gitignored**. Real values were copied locally so
the app runs out-of-the-box; only the `*.example.*` templates are committed.

| Real file (gitignored)         | Template                                | Holds               |
|--------------------------------|-----------------------------------------|---------------------|
| `shared/appsettings.json`      | `shared/appsettings.example.json`       | Schwab API keys     |
| `shared/tokens.json`           | `shared/tokens.example.json`            | Schwab OAuth tokens |
| `shared/sentiment_bridge.json` | `shared/sentiment_bridge.example.json`  | Sentiment bridge    |

`schwab-proxy/proxy_tokens.json` and `**/config_notifications.py` are also
gitignored. **Never commit real keys, tokens, or account numbers.**

## Running

The simplest path is `start_all.bat` (Memurai check → proxy → sentiment_svc →
options_svc → portfolio_svc → trade_svc → driver_svc → web gui, opening the
browser). It opens the proxy + 5 services + web gui in **7 separate console
windows**.

**One-window alternative — `start_all_wt.bat`** (requires Windows Terminal):
launches the same 7 processes as **7 tabs in a single Windows Terminal window**
(live logs preserved, but far less desktop clutter). The processes stay 7
separate OS processes — required, since merging services into one Python process
would re-introduce the `config`/`scoring`/`notifier`/`src` top-level
module-name collisions the 3-tier split exists to prevent. Each tab waits for the
proxy (:8100) before starting via `tools\wait_and_run.bat <wait_port|0> <script>`
(the proxy tab passes `0` to start immediately), preserving the same ordering as
the multi-window launcher; tabs run under `cmd /k` so they stay open with live
output. Close the window (or a tab) to stop the services.

**Stopping — `stop_all.bat`** (also reachable from the GUI's **More → Terminate**
page): runs `tools\stop_all.py`, which reads the ports from `repo_paths` and kills
whatever is LISTENING on the proxy + 5 service ports + the web GUI (web GUI last).
**Memurai (:6379) is intentionally left running** — it's a shared Windows service,
not something this repo starts. The Terminate button spawns the batch fully
detached (`cmd /c start`) so it isn't in the web app's process tree (it taskkills
the web app itself, so a child would otherwise kill itself mid-run).

Manual order:

```powershell
# 1. Activate the venv
.\.venv\Scripts\Activate.ps1

# 2. Memurai (Redis backbone) must be running on :6379 — it installs as a native
#    Windows service; start it from services.msc if needed. (3-tier cache/pub-sub/commands.)

# 3. Start the proxy (waits to bind :8100) — everything reads market data through it
python schwab-proxy\schwab_proxy.py

# 4. Start the migrated domain services (each owns its refresh/scheduling; publishes to Redis).
#    Sentiment + Options + Portfolio + Trade + Driver are all migrated.
python services\sentiment_svc\app.py      # :8210  (composite + rotation)
python services\options_svc\app.py        # :8211  (scan/swing/header/gamma/paper/captured/calculator
                                          #          + 5-min intraday GEX history collection, 08:30–15:20 CT)
python services\portfolio_svc\app.py      # :8212  (sector breakdown + vs-sector perf + live-streaming P&L)
python services\trade_svc\app.py          # :8213  (on-demand symbol analysis: MTF + Position/Investor verdicts)
python services\driver_svc\app.py         # :8214  (morning-agent order-approval queue: 09:28-ET run + approve/skip;
                                          #          orders simulated — config.PAPER_TRADE=True)

# 5. In another terminal, start the NiceGUI app (reads cache:* from Redis; no engine imports)
python webgui\main.py      # serves http://127.0.0.1:8500
```

> **3-tier note:** Once a domain is migrated, the web GUI no longer computes anything
> for it — its **service must be running** (and Memurai up) or the page shows a
> "Waiting for … service" placeholder. **Sentiment, the entire Options section,
> Portfolio, Trade, and Driver are migrated** (`services/sentiment_svc`,
> `services/options_svc`, `services/portfolio_svc`, `services/trade_svc`,
> `services/driver_svc`) — **every page now reads Redis**; the webgui imports
> ONLY `nicegui` + `shared.bus` + `shared.contracts` — no app engines, so the documented
> `scoring`/`notifier` cross-app collision can no longer occur. See the "Planned 3-tier
> architecture" section.

## Performance characteristics & known hotspots

Single-user, localhost Memurai — so most of these are *tolerable today* but are the
real levers if a page feels sluggish or a service churns CPU/network. Audited
2026-06-19; ranked by impact. Fix the High items first if optimizing.

**Costing model (important, non-obvious):** the version is stored in a SEPARATE
tiny Redis key (`{key}:ver`, `INCR`'d by `cache_set`), so version-polls are now
**cheap probes** — `bus_client.read_version()` → `Bus.cache_version()` reads just
that int (no payload deserialize); `read_versions()`/`Bus.cache_versions()` batch
many in one pipelined round-trip (use it when a page polls several views — e.g.
Gamma). `read()`/`read_full()` still deserialize the full envelope (use only when
you actually need the payload). `Bus.cache_set(key, payload, event=…, skip_unchanged=…)`
(a) **skips the whole write + publish when the payload is byte-identical**
(`skip_unchanged=True` → no `INCR`/`SET`/publish, version unchanged → GUI poller
doesn't repaint), and (b) pipelines `SET`+`PUBLISH` into one round-trip when `event`
is given. The `SET` can't fold into the `INCR` (the envelope still embeds the version
for `cache_get`). options_svc header + gex_status use `skip_unchanged`; other periodic
republishers (sentiment 120 s, portfolio per-tick, driver perf) still bump
unconditionally — opt them in the same way if they prove chatty.

**HIGH — webgui event-loop pressure (runs on *every* page):** *(FIXED 2026-06-19)*
- The app-wide 2 s watcher now runs its blocking bus reads **off the event loop**
  (`main._tick` is async → `run.io_bound(_watcher_compute)`); `_watcher_compute`
  reads `options:scan` **once** and passes it to `_recompute_badges(scan)` (no double
  read), reads the driver badge via `bus_client.read_full` (payload+version in one
  read), and uses the **in-memory-cached** `app_settings.load()` (no per-tick disk
  read; invalidated on `set()`). Badge/chime UI work happens back on the UI thread
  after the await.
- `proxy.health()` is memoized for `_HEALTH_TTL_SEC` (`main.cached_health`) and
  re-warmed off-thread by the watcher tick, so a navigation no longer makes a blocking
  3 s-timeout HTTP call before first paint.
- `pages/options/gamma.py` coalesced its **four** 2 s version-polls into **one**
  `_poll` that reads all four versions in a single pipelined `read_versions(...)` call
  (cheap `:ver` counters) and dispatches only the changed views. (`status.py` remains
  the model citizen — blocking sweep via `nicegui.run.io_bound`.)

**HIGH — service-side serial proxy fan-out (biggest wall-clock wins):** *(FIXED
2026-06-19)* These I/O-bound proxy loops now fan out concurrently via
`services/_parallel.py:parallel_map` (services) / an inline `ThreadPoolExecutor`
(engine files). The proxy rate-limiter only *spaces* upstream calls ~0.2 s apart
(it does **not** hold a lock across the Schwab round-trip — see `schwab_proxy.py:
_rate_limit`), so concurrent calls genuinely overlap; pools are kept ≤8.
- Sentiment sector load — the 11 `get_daily_history` + 11 `/chains` loops in
  `sentiment_svc/compute.load_sector_perf` + `load_industries` (extracted to shared
  `_fetch_closes`/`_fetch_pcr` helpers), and the per-sector chains+history loops in
  `live_composite.compute_live`. *(The `_load_all_industries` outer per-sector loop
  stays serial — each iteration's inner fetches are now concurrent; flatten later if
  needed.)*
- Portfolio — `portfolio_svc/compute.compute_baselines` (per-symbol) and the
  `build_portfolio` holdings build (`portfolio-analyzer/src/portfolio.py`) fan out
  concurrently. **Plus:** baselines now recompute only when
  `compute.baseline_signature` (equity holdings + entries + day) changes — the
  periodic 10-min rebuild reuses cached baselines when nothing changed (`state.
  baseline_sig`), instead of re-fetching ~2N histories every cycle.
- Trade analyze — `_fetch_timeframes` (5 timeframes) and the independent
  SPY + sector-ETF + fundamentals fetches in `analyze` now run concurrently
  (after the early daily-sufficiency gate, so output is unchanged).

**HIGH — service-side per-tick churn (runs 24/7, no browser needed):** *(FIXED
2026-06-19)* `options_svc` `refresh_header` (a `get_quotes` proxy call + bridge read)
+ `publish_gex_status` (SQLite read) used to run on **every 30 s tick with no
market-hours gate** → ~2 proxy calls + 1 DB open + 2 cache writes every 30 s, all
day/all weekend. Now gated by `scheduler.periodic_refresh_due` — every tick during
market hours, throttled to once per `_OFFHOURS_INTERVAL_MIN` (5 min) off-hours/
weekends — **and** both use `cache_set(skip_unchanged=True)` so an unchanged view
writes/publishes nothing. The remaining per-tick proxy/DB churn outside market hours
is ~1/10th of before. *(Other services' serial fan-outs below are still open.)*

**MEDIUM — engine compute:** *(all FIXED 2026-06-19)*
- `shared/analysis_lib/technical.calculate_ema` is vectorized via `.ewm` (SMA seed +
  `ewm(alpha, adjust=False)` from the seed = the former loop's exact values, no Python
  loop). `macd_histogram_series` computes the histogram once; trade analyze reads
  `[-1]`/`[-2]` from it (was two `calculate_macd` calls = 4 EMA passes → 2).
  `volume_profile` buckets via `np.digitize`+`np.bincount` (was O(bars×bins) nested
  `iterrows`). Characterization tests pin numeric equivalence
  (`services/trade_svc/tests/test_technical.py`).
- `shared/bus.consume_commands` creates the consumer group **once** per (stream, group)
  per Bus (`self._groups`), not on every ~50 ms poll.
- Static workbooks: `sectors_ref.load_sectors_data` is now mtime-cached (`reset_cache()`
  for tests). (`watchlist.get_scan_symbols` was already mtime-cached — the `Top 20.xlsx`
  GEX-poll read only `stat()`s, never re-parsed.)
- `schwab_proxy.trader_request` routes through the pooled `token_mgr.session` (was bare
  `requests.*` → fresh TLS per `/accounts`/`/positions`/`/orders` call).
- `gex_history_db.load_today` / `load_today_with_grid` use a sargable
  `ts >= ? AND ts < ?` range (`_today_local_unix_range()`) so the `ts` index applies,
  instead of `DATE(ts,'unixepoch','localtime')=DATE('now')`. *(Not done: per-worker
  SQLite connection reuse — opening a read-only connection is sub-ms and sharing one
  across the service's executor threads would need `check_same_thread=False`+locking;
  deliberately left as a fresh connect per read.)*

**Already done right (don't "fix"):** all data pages version-gate repaints; Gamma and
Sentiment charts update Highcharts in place (`el.options=…; el.update()`, no flicker); `ui_guard`
suppresses dead-client callback noise; portfolio's SSE loop only republishes on a
`dirty` flag; the Redis connection and the marketdata Schwab session are pooled
singletons. **Hygiene:** stale `*.log.err` manual stderr captures are now
`.gitignore`-d (and the old ones removed).

## Tests

Each app's tests run from **inside that app folder** (entrypoints add the repo
root to `sys.path` at runtime):

```powershell
cd schwab-proxy        ; python -m pytest tests
cd options-scanner     ; python -m pytest tests
cd sentiment-dashboard ; python -m pytest tests
cd trade-analyzer      ; python -m pytest .
cd portfolio-analyzer  ; python -m pytest tests
cd claude-driver       ; python -m pytest .
cd webgui              ; python -m pytest .   # 552 tests: transforms + shell smoke
```

The 3-tier services run per folder from the repo root (NOT `pytest services` over
all of them — that puts multiple hyphenated app dirs on `sys.path` at once and
re-triggers the documented `config`/`scoring`/`notifier` module-name collisions):

```powershell
# from the repo root, one service at a time
.venv\Scripts\python -m pytest services\sentiment_svc   # 26
.venv\Scripts\python -m pytest services\options_svc     # 285
.venv\Scripts\python -m pytest services\portfolio_svc   # 27
.venv\Scripts\python -m pytest services\trade_svc       # 56
.venv\Scripts\python -m pytest services\driver_svc      # 138
.venv\Scripts\python -m pytest shared\bus               # 15
.venv\Scripts\python -m pytest shared\contracts         # 37 (no app-dir imports — safe together)
```

- **options-scanner** has ~2 known date-relative failing tests carried over from
  the source repo — do not "fix" them as part of unrelated work.

## External processes (not in this repo)

The ML prediction servers (MES 8000 / MNQ 8001 / ES 8004 / NQ 8005) and the
options analytics service on 8200 are **separate, external processes**.
claude-driver addresses them over HTTP; this repo does not contain or start them.

## Design / plan docs

- [`docs/plans/2026-06-15-three-tier-architecture-design.md`](docs/plans/2026-06-15-three-tier-architecture-design.md) — **3-tier re-architecture** (GUI / per-domain services / Redis-Memurai backbone)
- [`docs/plans/2026-06-15-three-tier-architecture-plan.md`](docs/plans/2026-06-15-three-tier-architecture-plan.md) — bite-sized TDD implementation plan for the above
- [`docs/plans/2026-06-14-nicegui-webgui-design.md`](docs/plans/2026-06-14-nicegui-webgui-design.md)
- [`docs/plans/2026-06-14-nicegui-webgui-plan.md`](docs/plans/2026-06-14-nicegui-webgui-plan.md)
