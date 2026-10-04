# Paths, ports and configuration files

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

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
nicegui = 8500            # the NiceGUI app
nicegui_live = 8501       # the PUBLIC read-only screens (a second NiceGUI process)
memurai = 6379            # Redis backbone (Tier 3)

[services]                # Tier-2 domain services (repo_paths → SERVICE_PORTS/SERVICE_URLS)
sentiment = 8210
options   = 8211
portfolio = 8212
trade     = 8213
market    = 8215
news      = 8216
```

**STANDING RULE — configurable by default (the user's instruction, 2026-09-19).**
Where it is possible, a value an operator could reasonably want to tune — a
threshold, a window, a cadence, a limit, a symbol list, a model name, a TTL —
lives in a `config/*.toml` file read through `shared/config_toml.toml_loader`,
**not** as a literal in code. Two obligations come with it:
1. **New code** puts every such value in config from the start.
2. **Code you touch** that already hard-codes one moves it while you are there.
A value that goes into a TOML must also get an entry in
`webgui/config_schema.py`, the catalogue behind **Settings → Configuration**, so
the operator can see and edit it with a plain-English label and help text. A key
missing from the catalogue still works, but it is invisible in the app, and
`webgui/tests/test_config_schema.py` fails on it. The exceptions are values that
are not operator choices: mathematical constants, contract/unit conventions
(100 shares per contract), data-driven colour maps, and anything whose change
needs code to follow it.

**Rule: never hard-code `D:\` paths or port numbers in the apps.** Add them to
`repo_paths.py` / `config/ports.toml` and import them.

`config/commissions.toml` is the single source of truth for **commission rates**
(Schwab standard: options $0.65/contract per leg, futures $2.25/side, index-exchange-fee
passthrough), loaded by `services/options_svc/commission.py` (used by the Rescue
candidate menu). **Rule: don't hard-code commission rates** — add them here.

`options-scanner/options_calculator.py` holds **`RISK_FREE_RATE = 0.045`**, the single
source for the pricing `r` used by the calculator, the simulator, `gamma_tool`,
`options_svc.compute` (`calc_iv` + the projection band) and `backtest_0dte`. **Rule:
import it; never re-declare a rate literal.** It is code, not TOML, because every
consumer is a pricing module that already imports `options_calculator` — a config round
trip would buy nothing. ⚠ This rule is written down because the codebase *thought* it
already held: the 2026-07-01 accuracy audit closed finding **C7 ("single-source `r`")**
as FIXED, and `test_expiry_time_rate_consistency.py` documented "a single `RISK_FREE_RATE`
source of truth" — while `gamma_tool` still carried five `0.045` literals and
`backtest_0dte` its own `RISK_FREE = 0.04`. The test only ever checked the three modules
that had been converted, so the claim and the guard were both narrower than they read.
A **source-level** guard in that file now fails on any new rate literal in the pricing
modules, which is the part a value check cannot do.

**A TZ-NAIVE datetime in this project means CENTRAL time, and getting that wrong
cost an hour of phantom option value on three screens (fixed 2026-08-20).**
`options_simulator/data.py` builds its price-history index with
`.tz_convert("America/Chicago").tz_localize(None)` — the documented project-wide
convention — and `as_of=datetime.now()` is host-local on the CT box. But
`options_calculator.expiry_time_to_years`'s naive branch settled against a **naive
16:00**, i.e. 16:00 CT = **17:00 ET**, an hour past the real close. Every Replay
bar and IV-shock theo priced with **T + 1 hour** (a 0-DTE ATM at 14:00 CT showed
$5.80 against a true $4.10, and options still carried time value 30 minutes after
they were dead). The naive branch now **localizes to `NAIVE_WALLCLOCK_TZ`** so
both branches resolve to the one settlement instant, 16:00 America/New_York.

⚠ **This was introduced by a "fix".** Audit item C6 moved that branch from
`hour=15` to `hour=16` and wrote tests asserting a naive 13:00 leaves three hours
— which silently asserts the naive clock is Eastern. The pre-C6 `hour=15` had been
**correct for CT input all along**. The lesson is in the test shape: a test over a
naive datetime cannot state which zone it means, so it pins whatever the code
does. The guard that actually holds is
`test_naive_and_aware_paths_agree_on_the_same_instant` — a naive CT wall-clock and
the tz-aware datetime for the same instant must return the same T.

**Two more time-basis bugs shared that root and are fixed with it.**
`_leg_expiry_years` (the Calculator's P&L grid) hand-rolled its own
`datetime(y, m, d, 16)` against a naive `now()`, so the grid's "Now" column
disagreed with the summary tiles above it and the T=0 "Exp" column printed ~$4/share
of time value instead of the kinked payoff; it now delegates to
`expiry_time_to_years`. And the Simulator's **What-if** sweep took whole-day DTE
(`(exp - today).days`) floored at 0.01 days, so every 0-DTE leg — and the P/L
baseline it is measured against — priced at T ≈ 14 minutes no matter how many
hours remained: a **4.6× understatement** at five hours to the close, on the same
page whose other two engines were intraday-aware. It now calls the new
`compute._leg_days_to_expiry`. **Rule: never compute a time-to-expiry inline.
There is one settlement instant (16:00 ET) and one helper per tier —
`options_calculator.expiry_time_to_years` and `options_svc.compute.time_to_expiry_years`.**

⚠ **`proxy_client`'s `get_intraday_history` / `get_daily_history` return NAIVE
UTC** in their `datetime` column (`pd.to_datetime(ms, unit="ms")`), so they break
the convention above the moment a naive stamp reaches a pricer. The Replay path
did exactly that until 2026-09-11 — every bar priced five hours late, a 0-DTE
option "expired" by 10:00 CT — and `compute._replay_index` is the conversion to
reuse. Daily candles are stamped at midnight **Central** (05:00/06:00 UTC), so
converting keeps their date. A naive pandas `Timestamp.timestamp()` reads as
UTC, which is why the Expected Move path's epoch-ms happened to come out right.

**Four config files were extracted on 2026-08-21 and a fifth added 2026-10-03, and
all five exist because the value is shared by modules that CANNOT import each
other.** That is the
test for whether a value belongs in a TOML here: a config file genuinely
deduplicates a cross-tier constant, where moving a single-consumer constant just
relocates it.

| file | holds | read by |
|---|---|---|
| **`config/trade_mgmt.toml`** | stop/target rules — TP fraction, stop multiple, delta drift + hard ceiling, cut-DTE, the trail ladders, plus `[structures.*]`, the PER-STRUCTURE overlay on all of them | `options-scanner/signal_recommender.py` (auto-manage) **and** `options_svc/rescue.py` (the at-risk board) |
| **`config/scanner.toml`** | selection floors — IV-rank minimums, per-VIX-regime credit floors, directional delta band, score cutoffs | `scanner_engine.py`, `signal_recorder.py`, `options_svc/compute.py` |
| **`config/symbols.toml`** | the traded universe — GEX collection list, Net-Prem display groups, the BIG10 basket | `gex_collector.py`, `options_svc/net_premium.py`, `market_svc/symbols.py`, **and Tier-1 `webgui/pages/options/gamma.py`** |
| **`config/sectors.toml`** | symbol → GICS sector, behind the paper engine's SECTOR cap. A file because nothing here derives a sector, and the workbook that existed covered 48 of 80 watchlist names | `shared/sectors.py`, read by `options-scanner/paper_concentration.py` |
| **`config/marketdata.toml`** | the proxy's local market-data store — `mode` (`off` / `shadow` / `on`) and the age limits and size bounds for chains, quotes and daily bars — plus the autoscan's wide fetch (`[scan]`), the collector's tail interval and carry limits (`[collection]`) the proxy's paper-trade tracker retry limits (`[tracker]`) and the order its Schwab calls are sent in (`[limiter]`). ⚠ Read at CALL time through `shared/marketdata_config.py`, so a saved change needs **no restart** — the exception to trap (1) below | `schwab-proxy/market_store.py` (through `schwab_proxy`'s gateway), `options-scanner/scanner_engine.py`, `options_svc/compute.collection_tiers` (which hands the collector its tiers) |

Plus **`config/sessions.toml` gained `[slots]`** — the scheduled Claude-analyze
briefings, the thrice-daily action digest, the nightly momentum cascade, and the
nightly **`calibration`** rebuild (16:30 CT, after `[windows.collection] stop`
so the day's outcomes have settled — it reads `signals.db` only and costs no
Schwab or Claude call), and the once-daily **`income`** scan (08:52 CT, kept off the 08:45 rescan — the
30–45 DTE window, against the ~690 `/chains` calls the autoscan cadence would
cost; since 2026-09-14 that is more than one call per symbol — each adds an
expiration-list call, and a symbol listing daily expiries needs several fetch
runs), and **`paper_settle`** (15:05 CT — the paper books' expiry settlement;
see "Expiry settlement has ONE rule"), and **`token_watch`** (07:30 CT, every
day — a systemd timer that warns before the Schwab sign-in lapses). They
are named clock marks, the same thing `[windows]` already models, and **each
`analyze` slot is a paid Claude call** while `income` is the largest scheduled
Schwab spend on that table, so it is the direct control on both.

**`shared/config_toml.py:toml_loader(path, defaults)` is the one loader.** It
returns `(load, reset)` and encodes the contract every config file here follows:
built-in defaults are the real values and the TOML only overrides · deep-merged
so a file setting one key keeps every sibling · mtime-cached · **never raises**.
`flow_alerts.py` and `market_calendar.py` still carry their own older copies of
that logic; new config goes through the factory.

**The operator OVERRIDE layer (2026-09-19): `config/local/<name>.toml`, gitignored.**
Load order is built-in defaults ← tracked `config/<name>.toml` ← local override,
and **every** loader honours it — the factory, the two older copies above, both
commission modules and `theme.load_theme` all read through
`config_toml.read_layered`, with `layered_mtime` as the cache key so a saved
override is seen without a restart where the value is not a module constant.
Settings → Configuration (and the Appearance editor) write ONLY the override,
through `config_toml.write_overrides` (atomic, round-trip-checked), and only
values that differ from the shipped file. ⚠ **Never make the app write a tracked
config file**: it dirties the prod checkout and `tools/promote.sh` refuses a dirty
tree — the Appearance editor did exactly that until 2026-09-19. ⚠ Under pytest the
layer is **ignored** (`"pytest" in sys.modules`, not `PYTEST_CURRENT_TEST`, because
module-level constants resolve at collection time), so a tuned prod checkout still
tests the shipped values; a test of the layer sets
`TRADING_CONFIG_OVERRIDES_IN_TESTS=1`. `config/local/` is in
`backup_local.DATA_TREES`, and `changes.jsonl` there is the edit history. ⚠ A loader
added outside these must use `read_layered`, or the page will save a value the
service never reads. ⚠ `load()` hands back the
CACHED mapping, so **treat a config dict as read-only** — copying on every
hot-path read would defeat the cache.

⚠ **Two traps when wiring one of these.** (1) The consumers keep module-level
CONSTANTS resolved at import (`MIN_IV_RANK = _scfg.min_iv_rank()`), matching the
"edit + restart" contract — so a test that merely asserts `settings.X ==
config.X` **proves nothing**, since the literal it replaced had the same value.
The discriminating test monkeypatches the accessor and `importlib.reload`s the
consumer. Every one of these extractions ships with that test, because the first
draft of each passed green before the code was wired. (2) The shapes the engines
index are preserved deliberately — `MIN_CREDIT_PCT["0-DTE"][regime]`,
tuple delta bands, a tuple `SINGLE_LEG_EXCLUDED_GRADES`, tuple-of-dicts
`netprem_groups` — TOML gives lists and flat tables, so the shared modules convert
rather than making every call site change.

**`shared/symbols.py` is now on the Tier-1 allow-list**, alongside
`shared.market_calendar`. `webgui/pages/options/gamma.py` used to hold a
deliberate byte-copy of `net_premium.GROUPS` under a comment explaining that Tier
1 may not import `services.*`, with tests as the only thing keeping the two in
step. **Reading a config FILE is not a `services` import** — and `theme.toml` is
the standing precedent for Tier 1 doing exactly that — so the duplication is gone
rather than merely policed.

`config/theme.toml` is the single source of truth for the **webgui styling palette**
(surfaces/cards/text, buttons, semantic state colors, the
Sentiment/Rotation chart palette), loaded once at webgui
startup by `webgui/pages/options/theme.py:load_theme()` — edit + restart the webgui to
restyle without code changes; missing keys fall back to the built-in dark-navy defaults.
See the "App theme — dark-navy 'dashboard'" section. **ONE section is still a page-scoped language, NOT the app-wide palette and NOT
surfaced in Settings → Appearance:** `[flow]` — the Options Flow console panels, the
`/options/gamma` Flow + Net Prem subtabs only (builder `flow_colors` +
`FLOW_KEYFRAMES_CSS`, injected via that page's ONE `ui.add_css` escape-hatch block).
⚠ **It survives for a reason the others did not have:** both panels are built as ONE
raw `ui.html` SVG fragment each, the documented out-of-scope case for the
Tailwind-first rule — so even its `title` / `label` / `panel_*` / `grid` keys, which
would read as surface anywhere else, are INSIDE the chart. `call` and `put` are also
byte-identical to `gamma.POS_COLOR` / `NEG_COLOR`, so the plasma heatmap and the Flow
panel read as one instrument across a subtab switch.

**FOUR more sections survive as DATA ONLY** (2026-09-20, Phases 2, 3 & 4 of the UI
consistency work): `[console]` keeps its semantic set, the six `regime_*` hues and
`accent` (the dial arc); `[sectors]` keeps `up` / `dn` / `warn`, the regime word and
its dot; `[macro]` keeps the risk-on/off tile colours; and **`[calc]` went 33 keys to
four** — `pos`, `neg`, `accent`, `warn` — losing `.calc-v3`, `build_calc_css`,
`CALC_KEYFRAMES_CSS` and its JetBrains Mono link. ⚠ Nothing it encoded was lost:
`leg_editor.DEFAULT_LEG_TOKENS` and `entry_panel.DEFAULT_PANEL_TOKENS` already carried
bid-green, ask-red, ATM-amber, the ITM wash, long-cyan/short-green and the typed-price
amber, and the P&L matrix ramp was never in the TOML at all. ⚠ `.calc-v2` was never the
Calculator's either — it was the shared navy scope — and it is **gone** as of
2026-09-20: `pages/trade.py`'s unrouted `render` held the last element wearing it, and
the class died with that function. Their background, text, button
and font keys are gone, because `/sentiment`, `/desk`, `/symbol`, `/sentiment/sectors`
and `/market` now wear the app surface and IBM Plex. **`[rotation]` is gone entirely**
— `void`, `panel` and `font_url` were its only keys and all three were surface.

⚠ **The neutral ladder those screens shared went with it.** `rotation_view.NEUTRAL`
→ `NT` / `NB` / `NE` was a warm-neutral lightness ladder carrying text, edge and
background roles across RRG, Rotation, Bull/Bear and Momentum. It was load-bearing on
four screens with **no guard of its own**, and no test anywhere pinned it. What
survives in `rotation_view` is data — `QUAD_HUE` / `QUAD_CHROMA` and `TONE` — and note
**`TONE["flat"]` had to be given its own neutral first**, because it was built out of
the ladder and would have died with it.

⚠ **A neutral is SURFACE wherever it lives, and three of them survived the first
sweep by their ADDRESS rather than their nature.** `LEVEL_GROOVE`, `LEVEL_TRACK` and
`ALIGN_OFF` sat in `momentum_view` rather than `rotation_view`; measured in the
harness, the groove covered 566,000px of the Momentum page at 1.094:1 against the app
gradient — a warm near-black card directly under a navy control bar. The rule that
settles it: **chroma ≤ 0.01 at hue 90 is that ladder whatever file it sits in.** The
same test then caught `_ROW_RULE` / `_HEAD_RULE` on the Desk (hairlines *darker* than
the app's card, which read as smudges), `MB_FAINT` / `MB_PANEL_BG` / `MB_TILE_BG` on
the Macro Board, and two sites the greps had missed because they were spelled with
single quotes — the Desk's row hover washes and `shell.PANEL_SCROLL_CSS`, whose hexes
had been sampled off the console's gradient card.

Their colour ramps are deliberately NOT config-driven: both are data-driven cell maps
(the category excluded above, alongside the gauge face and the score/heat/P&L zone
maps), living in `webgui/pages/sector_heat.py` and `webgui/pages/rotation_view.py`.
`webgui/pages/oklch.py` holds the oklch→sRGB conversion both use — both supplied
designs were authored in oklch, and both sit at the dark end of the range where an
sRGB interpolation visibly bunches the low steps. ⚠ The heat ramp's flat step was
chosen to sit one hair above the OLD `#080808` ground; measured against the app navy
on 2026-09-20 it reads **1.025:1** (the strongest cell 1.24:1), so it still fades into
the page and was deliberately left un-re-anchored.

**There is now ONE quadrant palette** — `rotation_view.QUAD_HUE`/`QUAD_CHROMA`
(Leading 158 / Improving 232 blue / Weakening 80 olive / Lagging 22), imported by
`rrg_view` and `momentum_view` so the Rotation, RRG and Momentum screens cannot
drift. The old local set (`sentiment_rotation.quadrant_color`, Leading `#66bb6a`
/ Improving `#3fb6c7` cyan / Weakening `#ffd54f` / Lagging `#ef5350`) was
**deleted on 2026-08-17**: its two consumers were the Highcharts RRG scatter and
the Sector & Industry RRG column, and both went in those pages' rebuilds. This
closes the "two palettes" question that redesign opened. `pages/sentiment.py`'s own
`rrg_color`/`rrg_text_class` went in the same 2026-08-17 cleanup.

`config/flow_alerts.toml` is the single source of truth for the **options-flow alert
thresholds** (crossover `band`/`min_premium`/`cooldown_min`; UOA `k`/`vol_floor`/
`premium_floor`/`top_n`; **gamma_flip `enabled`/`band_pct`/`cooldown_min`/`symbols`** — the
dealer gamma-regime flip alert; the `enabled` server kill-switch), loaded by
`services/options_svc/flow_alerts.py:load_thresholds()` (defaults if the file is missing) —
edit + restart `options_svc` to tune. See the 2026-07-18 + 2026-07-22 "Last updated" entries.

`config/sessions.toml` is the single source of truth for **market session windows +
the extended-hours activation date** (2026-08-02). All times are **CT** (ET and CT
shift together for DST, so the values are stable year-round). It holds
`[activation] extended_hours_from` (**2026-08-17** — every ETH branch is inert
before it, so a Cboe slip is a one-line edit), the three sessions
(`[sessions.gth|regular|curb]`), the named operating windows
(`[windows.scan|collection|session_flip|market_snapshot|…]`, each
optionally carrying its own `tz` and `end_exclusive` — no shipped window sets
either since the driver's `driver_entry` went, 2026-09-22), and
`[alerts] fire_in_extended_hours`. Loaded by
**`shared/market_calendar.py:load_config()`** (mtime-cached, mirroring
`flow_alerts.load_thresholds`; a malformed file degrades to built-in defaults for
bad **values and bad shapes** and never raises). **Edit + restart the affected
service.** One knob is load-bearing and easy to get wrong:
`[windows.session_flip].at` is held SEPARATE from `collection.start` so widening
GTH collection can't silently move the Gamma display flip. See the 2026-08-02 "Last updated" entry.

**`webgui/pages/fmt.py` is the shared numeric vocabulary** — `num` (strict: a real
reading or None, rejecting NaN AND bool, since `float(True)` is 1.0), `float_or`
(permissive: coerce with a fallback, NaN passes through), `clamp`,
`round_or_none`, `fixed`, `signed_pct`. ⚠ **`num` and `float_or` differ on
purpose and a test pins it**: use `num` for anything feeding a comparison, a
colour or a direction; use `float_or` when you have a sensible fallback and the
value is about to be formatted. `num` alone had SIX byte-identical copies before
2026-08-20. Options table helpers (`rescue_highlight`, `AT_RISK_STATES`) live in
`pages/options/rescue.py` beside `heat_border_class`.

**`webgui/pages/copy.py` is its sibling for shared SENTENCES** (2026-09-04) —
text more than one screen shows for one condition, where two screens wording it
differently is a defect rather than a style difference. It holds
`WAITING_OPTIONS` / `WAITING_SENTIMENT` / `WAITING_MARKET` and imports nothing
from `pages`, which is what makes it safe to import at any depth: `desk.py`
imports `pages.options.flow` and `pages.options.matrix`, so neither of those can
import `desk` back. ⚠ **These are the lines for a feed that has published
NOTHING** — never for one that is fine and has nothing to report. Every screen
drawing one keeps its own quiet-market line (`desk.EMPTY_*`,
`flow.status_text`'s empty branch), because a dead service and a still tape
rendering the same words is what the "never print a zero you did not read" rule
exists to prevent. `webgui/tests/test_shared_copy.py` reads the source of every
page module and fails on a reintroduced literal — the guard a per-page test
cannot be, and it caught a site the hand-written grep behind it had missed.

**The screen vocabulary is written from the READER's side (the 2026-09-04
pass).** A label names what the number is FOR, not which mechanism produced it;
an empty state says what is true rather than which service is cold; and an
action toast says what to expect rather than that a command was enqueued. Two
traps that pass turned up repeatedly and are worth assuming on any page not yet
audited: **a `Credit` column is wrong wherever the book holds debits** (a debit
is stored as a NEGATIVE credit — hit on the Paper Ledger, Captured Signals, Paper
Account and the EOD report), and **an `entry_*` field rendered under a bare
label reads as live** (`dte_at_entry` under "DTE" on a tracking page). Per-page
decisions and the deliberate divergences — `/desk` keeping `STRAT`/`QTY` for
width, `/options/paper` keeping plain `P&L` because its rows can be closed — are
in the eight `docs/plans/2026-09-04-*-copy-design.md` docs, each pinned by test.

**The version-gate poll idiom is `webgui/pages/view_watch.watch_view(view, on_change)`**
— seed the version, probe the cheap `{key}:ver` on a timer, repaint only when it
moves. It was written out longhand on 22 pages; 4 (the sentiment screens sharing
the canonical shape) were converted 2026-08-20, the rest have genuinely different
shapes. ⚠ It deliberately does NOT swallow repaint errors — only the
deleted-client case, via `ui_guard`. Anything else propagates so NiceGUI logs it.
⚠ An `async def` `on_change` works only because the tick RETURNS its result for
NiceGUI's timer to await; a wrapper that calls it and drops the coroutine
repaints nothing, silently.

**`shared/market_calendar.py` is the single source of truth for the NYSE calendar**
(holidays **derived algorithmically** — no yearly edit) **and session/window
predicates**. Ten duplicated holiday sets and fourteen hardcoded window constants
were consolidated onto it. **Do not add a new holiday literal or window constant
anywhere** — add it here, or to `config/sessions.toml`. The two `tools/` copies went on 2026-08-20 (their justifying comment named the
wrong module — market_calendar pulls in no heavy deps, measured). The last site
outside it, `claude-driver/config.py`, was deleted with that folder on 2026-09-19.
`shared/` is a namespace package, so
`from shared.market_calendar import ...` resolves once the repo root is on
`sys.path`; legacy app-dir callers (`options-scanner/scanner.py`,
`scanner_engine.py`, `gex_status.py`) carry the three-line bootstrap.

⚠ **The marketing site consumes that calendar too, and its artifact is generated AND
COMMITTED.** `tools/generate_market_clock.py` emits
`deploy/site/assets/market-clock.js` — the holiday list plus the regular session
bounds, converted CT→ET — so a static page can decide open/closed in the visitor's
browser. It is **committed**, against this repo's own "generated state is gitignored"
pattern, because it is source a fresh clone needs to serve a working site, and nothing
on the serving box ever rewrites it; a test compares the committed bytes to the
generator's output, and that test **legitimately goes red on 1 January**, since the file
covers only the year it was generated in and the next. ⚠ **Early closes (13:00 ET, ~3
afternoons a year) are NOT handled, and the gap is deliberate** — nothing in this repo
models a half day, and the fix must not be a second hand-maintained date list, which is
what the rule above forbids.
