# SentimentDashboard — CLAUDE.md

> **Monorepo note:** This app now lives in the *Trading With Schwab* monorepo.
> Cross-app paths and service ports come from the root `repo_paths.py` (which
> reads `config/ports.toml`) — never hard-code `D:\` paths or ports; import
> them. The sentiment bridge is written to `repo_paths.BRIDGE_PATH`
> (`shared/sentiment_bridge.json`) and the proxy is at `PROXY_URL`
> (`http://127.0.0.1:8100`). See the root `CLAUDE.md` for the monorepo
> overview. Some older absolute paths mentioned below (e.g.
> `D:\AI_Based_Analysis\shared`, `D:\Schwab Test Project\OptionsScanner`) are
> historical and have been superseded by `repo_paths.py`.

Master guidance for working on the Sentiment Dashboard. Read this before
touching anything in this directory.

## Purpose

**In THIS monorepo this folder is ENGINES ONLY** - the pure `scoring/` package,
`live_composite.py`, `history_backfill.py`, `sectors_ref.py`,
`sector_rotation_assessment.py` and the bridge writer. It is imported by
`services/sentiment_svc`, which owns all scheduling and publishing.

**The tkinter desktop app was never copied into this repo.** Verified absent
2026-08-21: `sentiment_dashboard.py`, `notifier.py`, `Launch_Dashboard.bat`,
`sentiment_data.json`, `credit_cache.json`. Sections below that describe a UI
shell, a Live Data panel, autosave or a 15-minute Tk ticker are describing the
SOURCE repo (`D:\Trading With Schwab`), not this one - they are kept because the
scoring contracts they document are still exact. The interface here is the
NiceGUI webgui `/sentiment` family, which reads Redis and never imports this
folder.

## Architecture

```
services/sentiment_svc/     Owns cadence + publishing (composite 120s, trend 15m,
        |                   momentum nightly 16:20 CT). Reads these engines.
        v
scoring/                    Pure functions. No tk imports here.
  |- __init__.py            WEIGHTS dict - single source of truth.
  |- types.py               ScoreResult dataclass - the contract.
  |- vix.py                 term, vix1d, slope, complex
  |- put_call.py  breadth.py  rotation.py  sector_perf.py
  |- credit_pulse.py        (computed for display; NOT in WEIGHTS since v4.3)
  |- composite.py           blend, velocity, divergence
  |- market_state.py aggression.py effort.py session_structure.py
  |  rejection_defense.py profile_shape.py order_flow.py
  |                         the five-state classifier inputs
  |- momentum.py momentum_regime.py     the nightly cascade
  |- daily_direction.py     OFFLINE validation proxy only (validate_market_state.py)
  |- _common.py             shared safe_float / score_from_thresholds
        |
        v
live_composite.py           compute_live -> the live intraday composite
bridge.py                   write_bridge(payload) -> repo_paths.BRIDGE_PATH
        |
        v
shared/sentiment_bridge.json    (repo_paths.BRIDGE_PATH - the ONLY path)
        |
        v
options-scanner/regime_filter.py   the one remaining consumer; migrating it to
                                   cache:sentiment:composite is the last open
                                   item of the 3-tier migration.
```


## Component catalog (5 components, v4.3)

Source of truth: `scoring/__init__.py:WEIGHTS`. Mirror here for reference.
Weights sum to 100%.

| Component | Weight | Module | Notes |
|---|---|---|---|
| VIX Complex | 20% | `scoring/vix.py:score_complex` | Internal blend of three sub-scores: Term 50%, VIX1D 33%, Slope 17%. Sub-scores still displayed on VIX tab but no longer enter composite directly. |
| Put/Call (sectors) | 20% | `scoring/put_call.py` | **v4.3:** cap-weighted per-sector Put/Call from option chains (was 15% market $CPCE). Scored via the same `PC_THRESHOLDS`. |
| Breadth | 20% | `scoring/breadth.py` | NYSE A/D ratio + % above 50 DMA + H/L. |
| Rotation | 15% | `scoring/rotation.py` | Blended day/3d/week cyclical-vs-defensive (40/40/20). |
| Sector Performance | 25% | `scoring/sector_perf.py` | S&P cap-weighted daily move across 11 GICS sectors. Cap weights in `sectors_ref.SP500_SECTOR_WEIGHTS`. |

## Five-state market classifier (2026-07-07) — the intraday / regime trend

The bridge `trend_regime.state` + the webgui **Today** Market-Trend label are now driven by a
**two-axis direction × aggression** classifier (Bullish / Lack of Bullishness / Neutral / Lack of
Bearishness / Bearish), NOT the old `trend_regime` bands (which are RETAINED only for the 30-Day
structural gauge). New PURE `scoring/` modules: `market_state.py` (the 9-cell grid +
STATE_LABELS/DESCRIPTIONS), `aggression.py` (signed confidence-weighted blend), `effort.py`
(volume-effort), `session_structure.py`, `rejection_defense.py`, `profile_shape.py`,
`order_flow.py` (Lee-Ready aggressor quote rule + put/call option pressure), and
`daily_direction.py` (a daily direction-score proxy + reconstruction/IC helpers for the offline
validation). The 0–100 DIRECTION axis stays `intraday_trend.py` (its `score_to_state` banding is
kept for the 30-day gauge). The classifier is assembled + the aggression inputs read in
`services/sentiment_svc/compute.compute_intraday_trend`; state-transition phone alerts in
`services/sentiment_svc/state_alert.py` (via `shared/notify/`). Offline validation:
`validate_market_state.py` (run MANUALLY) → `data/market_state_validation.{md,json}`.
**The full design, data flow, and the honest backtest result live in the root `CLAUDE.md`
five-state entry** — read that first.

**Credit Pulse removed from the composite (v4.3).** Its 5% was reallocated to
Put/Call (15% → 20%). `scoring/credit_pulse.py` (HYG/IEI z-score 60% + HYG vs
50d MA 40%, 60d cache) and `history_backfill.py` still **compute** a
`credit_pulse` score for display/back-compat, but it is no longer in `WEIGHTS`
and does not enter the confidence-weighted blend.

## Momentum cascade (2026-07-28) — a SEPARATE subsystem, not a component

**Momentum is NOT in the sentiment composite.** `scoring/__init__.py:WEIGHTS` is
untouched, no component was added, and the bridge `component_scores` block does
not change. Momentum is *context*, published on its own cache key
(`cache:sentiment:momentum`), consumed by the webgui page. Anyone proposing to
fold it into `WEIGHTS` is starting a different, coordinated change — the
weights assert and the `tests/fixtures/bridge_v39_snapshot.json` regression
oracle both exist to make that deliberate.

**The pure modules live here:**

| Module | Role |
|---|---|
| `scoring/momentum.py` | The five components — `trend_strength` (Clenow slope × R²), `relative_strength` (excess + RS-line slope), `acceleration` (r21 − r63/3), `path_quality`, `participation` — plus `zscore_within_level` / `blend` / `percentile_rank`. `MOMENTUM_WEIGHTS` asserts to 1.0 at import, same invariant style as `WEIGHTS`. |
| `scoring/momentum_regime.py` | The gate — `dispersion`, `dispersion_percentile`, `classify` → a frozen `RegimeVerdict` (`favorable` / `neutral` / `suppressed` + the lookback that state implies). |
| `sectors_ref.load_stocks_data()` | The **Stocks** tab — 370 rows, 5 constituents per (sector, industry), 311 unique symbols. Plus `stock_symbols()` and `constituents_by_industry()`. |

**Two design points worth not re-litigating:**

- **`blend` renormalizes over the components actually PRESENT.** Participation is
  undefined at stock level; substituting a neutral 0 would bias every stock down
  against its own peer group.
- **`suppressed` is checked FIRST in `classify`.** A rebound off a low presents
  as contango + high dispersion, which would otherwise talk the gate out of the
  one warning that matters (there, the biggest losers rip hardest).

**Orchestration lives in `services/sentiment_svc`, not here** — `momentum_db.py`
(bars + scores), `compute.compute_momentum()`, `handlers.refresh_momentum`, and
**one nightly scheduler slot at 16:20 CT** (`scheduler.momentum_due`).
**Deliberately nightly, not on the 120 s tick:** daily bars change once a day, so
recomputing ~390 regressions every two minutes would be pure waste and would load
the proxy during RTH for no signal.

**Two liquidity floors, by role.** A STOCK must clear **$5M** 20d average dollar
volume ("can I hold a position" — several small caps on the Stocks tab cannot,
and would top the leaderboard on a thin-volume pop). An industry/sector **ETF**
only has to clear **$250k** ("is this price series trustworthy enough to
regress") — it is a measurement instrument and the trade is expressed through its
constituents. Using the stock floor for both was measured on 2026-07-28 to delete
**23 of 70 industry ETFs**, silently gutting a third of that cross-section.

**The industry level scores 70 ETFs, not 74.** Four of the Stocks tab's 74
industries name an ETF another industry already owns (`MJ`, `XRT`, `BETZ`, `VEGI`
are each listed twice in the workbook, and `load_sectors_data` dedupes ETFs
globally). Scoring one price series under two labels would put duplicate rows in
the cross-section and invent a "two industries agree" signal, so those four are
reported in `excluded` with `reason: "duplicate_etf"`; their constituents still
roll up to the sector level and are still scored individually.

Full design + plan: [`docs/plans/2026-07-28-momentum-cascade-design.md`](../docs/plans/2026-07-28-momentum-cascade-design.md)
/ [`-plan.md`](../docs/plans/2026-07-28-momentum-cascade-plan.md).

## Trend regime (v4.1)

> **Superseded for the webgui/bridge by the intraday Market Trend model
> (2026-06-19).** The webgui Sentiment tab and the bridge `trend_regime.state`
> are now driven by the directional **0–100 intraday trend score**
> (`scoring/intraday_trend.py` + `services/sentiment_svc/compute.py:compute_intraday_trend`,
> 15-min cadence, 5-state mapped). The daily `trend_regime.classify` below is still
> used to fill the **back-compat** `sma_*`/`drawdown` bridge fields (merged in
> `compute._bridge_trend`) and remains the engine the legacy tk app would use. See the
> root `CLAUDE.md` "Intraday Market Trend model" section + the
> `docs/plans/2026-06-19-intraday-market-trend-redesign-*.md` design/plan.

Independent of the sentiment composite. SPY-based 5-state classifier
published in the bridge as the top-level `trend_regime` block so Options
Scanner can switch scan recipes on it.

States: `bull_trend`, `pullback_in_bull`, `range`, `bear_rally`,
`bear_trend`. Each carries a fixed human-readable label and description
in `scoring/trend_regime.py:STATE_LABELS` / `STATE_DESCRIPTIONS`.

Pipeline:
1. `scoring/trend_regime.classify(spy_closes)` — pure 5-state rule tree
   (close vs 50/200 DMA, 20-bar slope of 200-DMA, 252-day drawdown).
2. `scoring/trend_regime.commit_state(raw, history, prev)` — 2-day
   hysteresis. The app holds `_trend_state_history` (persisted in
   `sentiment_data.json` under the same key with `_` prefix), so a
   restart preserves the rolling window.
3. `_build_bridge_payload` writes the committed state + raw + inputs to
   the `trend_regime` block. Bridge schema bumped 4.0 → 4.1.

Tune by editing module constants (`SLOPE_BULL_MIN`, `BULL_DD_MAX`, etc.)
at the top of `scoring/trend_regime.py`. Hysteresis length is
`HYSTERESIS_DAYS`.

SPY 12-month history is fetched once per refresh in `_fetch_worker`
under cache key `('SPY', 12)` and stored on `self._spy_history` as a
`list[float]` of closes. The existing `('SPY', 1)` consumers (rotation,
flow) are independent.

## Scoring conventions

- Scores are **integers 1–10**, contrarian (10 = max fear / opportunity, 1 = max greed / risk). Use `0` only when the input is undefined.
- Confidence is `float ∈ [0.0, 1.0]`. Missing data → `0.0`. Partial data → fractional.
- Piecewise mappings use **narrow neutral bands** (typically 0.95–1.05 of normal) so small moves through the breakpoint produce visible score changes.
- Composite is **confidence-weighted**: `composite = Σ(w·s·c) / Σ(w·c)`. A low-confidence component cannot dominate.

### Adding a new component (5-step checklist)

1. **Add the weight** to `scoring/__init__.py:WEIGHTS`. The assert at the bottom enforces `Σ = 1.0` — rebalance accordingly.
2. **Create `scoring/<name>.py`** exposing `score(inputs) -> ScoreResult`. No `tk` imports.
3. **Write tests** in `tests/test_<name>.py`: boundary case per piecewise breakpoint, missing-data case (returns 0/0.0), typical-day case.
4. **Wire it in the service**: call it from `services/sentiment_svc/compute.py` and carry its score + confidence into the published payload. (Older revisions said to edit `_build_composite_panel` in the Tk app - that app is not in this repo.)
5. **Extend the bridge payload** in `services/sentiment_svc/compute.build_and_write_bridge`: add `component_scores.<name>` and `component_confidence.<name>`. Bridge fields are additive-only - never rename or remove, `regime_filter` reads them.

## Bridge contract

- **Schema version**: `BRIDGE_SCHEMA_VERSION` constant in `bridge.py`. Bump on any
  non-additive change.
- **Path**: `repo_paths.BRIDGE_PATH` = `shared/sentiment_bridge.json`. **One path,
  imported, never written literally.** Older revisions of this file named
  `D:\AI_Based_Analysis\shared\sentiment_bridge.json` as "canonical" and a
  `SentimentDashboard\sentiment_bridge.json` "legacy mirror"; neither exists here.
- **Writers**: `services/sentiment_svc` dual-writes it each composite refresh
  (`compute.build_and_write_bridge`), and `options-scanner/gex_collector.py`
  spawns `publish_bridge.py` when the manual fallback collector runs.
- **Reader**: `options-scanner/regime_filter.py` (`composite_score`, `regime`,
  `trend_regime.state`). It reads the FILE, not Redis - which makes this a
  Tier-2 -> Tier-2 file side channel, the documented last shim of the 3-tier
  migration.
- **Fields are additive-only.** Removing or renaming one is a coordinated change
  with `regime_filter`.


## Daily flow

| Trigger | What happens |
|---|---|
| App launch | Load `sentiment_data.json` into fields. If weekday & 08:00–16:00 CT, autofetch live data 500ms later. Schedule 15-min ticker. |
| 15-min boundary (08:00–16:00 weekday) | Auto-refresh fires `fetch_schwab_data()`. |
| Any "Calculate All" or autofetch finish | `_autosave()` runs: writes `sentiment_data.json`, bridge file (both paths), appends today to `sentiment_history.json` (de-dup by date, 90-entry cap), refreshes sparkline + rolling averages + History tab charts. |
| App close | Silent autosave; no prompt. |

**There are no manual Save buttons.** The View History button opens a read-only Treeview of `sentiment_history.json`.

## Live Data panel (v4.1)

The five legacy input tabs (VIX / Options / Rotation / Breadth / Flow) were collapsed into a single read-only **Live Data** tab. Every field shows: `Label | Value | Source badge | ✎ Override`.

Source badges:
- `● Live` — populated by the most recent Schwab fetch.
- `✎ Override` — user has manually overridden the live value.
- `⊘ Manual` — no Schwab source for this field; manual-only.

Override flow: click `✎` → the value cell becomes editable + a `↺ Revert` link appears. Subsequent autofetches do **not** overwrite an overridden field. Click `↺ Revert` to restore the cached last-live value and resume auto-updates.

Implementation: `_FIELD_OPTIONS` (dropdown vocab), `_LIVE_FIELD_GROUPS` (section layout), `_live_fields`, `_overridden_fields`, `_last_live`, `_set_live()`, `_begin_override()`, `_revert_override()`, `_refresh_live_data_panel()` in `sentiment_dashboard.py`.

## Data sources

**Schwab API is the sole live data source.** Every scoring input is fetched from Schwab:

| Component | Schwab symbols used |
|---|---|
| VIX Complex | `$VIX`, `$VIX1D`, `$VIX9D` (+ 10d MA computed locally) |
| Put/Call | `$CPCE` (Equity) — see `CPCE_PROBE_RESULT.md` for verification status |
| Breadth | `$ADVN`, `$DECN`, `$NYHGH`, `$NYLOW`, `$SPXA50R` |
| Rotation | Sector ETFs (XLY, XLP, XLK, XLF, XLE, XLV, XLI, XLU, XLB, XLRE, XLC, XLY, SMH, QQQ, IWM, SPY) — 5d % spreads derive the four pair-dropdowns |
| Sector Performance | Same sector ETF quotes, S&P cap-weighted |
| Credit Pulse | `HYG`, `IEI` (60d closes, cached in `sentiment_credit_cache.json`) |
| Flow dropdowns | HYG, IEI, TLT, UUP 5d % changes |

The only non-Schwab read is `Sectors_Industries_ETFs.xlsx` — a static reference workbook loaded once at startup for the sector/industry ETF map. No web scraping, no FRED, no yfinance, no FinViz, no AAII, no Fear & Greed. Anything that used to come from those (e.g. the "Survey" component in pre-v3.9 docs) has been removed.

## Testing

```powershell
cd sentiment-dashboard; ..\.venv\Scripts\python -m pytest tests -q
```

**Baseline: 507 passed, 1 skipped, 0 failed** (2026-08-21). The skip is
`test_apply_sector_perf.py`, a module-level `importorskip("sentiment_dashboard")`
- it exercises the Tk entrypoint this fork never copied, so it can only pass in
the source repo. Required green before merging any change under `scoring/` or
`bridge.py`.

There is no Tk UI here to verify manually; check webgui behaviour on
`/sentiment` instead.


## Invariants

- `sum(WEIGHTS.values()) == 1.0` (enforced by assert at import time).
- Every scoring module publishes a confidence in `[0.0, 1.0]`.
- No `tk` or `tkinter` imports under `scoring/` or in `bridge.py`.
- Bridge fields are additive across minor versions; removals require a major version bump.
- `ScoreResult` dataclass is frozen — never mutated in place.
- `sentiment_history.json` is capped at 90 entries.
- Composite is computed by confidence-weighted blend, never by simple weighted average.

## Files in this directory

| File | Role |
|---|---|
| `bridge.py` | Bridge writer + schema version + path (via `repo_paths`). |
| `live_composite.py` | **Live intraday composite** (`compute_live`) + `signal_band` + `build_bridge_payload` + `publish_bridge`. Shared by the sentiment service and the GEX collector. No tk. The per-sector Put/Call chain fan-out (11 NTM `/chains`) is TTL-cached 15 min (`PCR_TTL_SEC` / `reset_pcr_cache`); an empty off-hours result is NOT cached, so the first post-open refresh still picks up real volume. |
| `publish_bridge.py` | Standalone headless entry: `compute_live` -> write the bridge. Run by the GEX collector each cycle in a subprocess (this dir on `sys.path[0]` so `import scoring` resolves HERE, not to options-scanner's `scoring.py`). |
| `history_backfill.py` | Historical daily scoring (`_score_one_day`). |
| `sectors_ref.py` | `Sectors_Industries_ETFs.xlsx` loader - sector/industry ETF map, S&P cap weights, the Stocks tab. mtime-cached. |
| `sector_rotation_assessment.py` | RRG-style rotation assessment (`compute_rs_momentum`, `RISK_THRESHOLD`). See the root `CLAUDE.md` - there are TWO RRG implementations with different momentum definitions. |
| `validate_market_state.py` | OFFLINE five-state validation study. Run manually; never imported by a service. |
| `scoring/` | All scoring logic. Pure functions. |
| `tests/` | pytest suite. Includes `fixtures/bridge_v39_snapshot.json` regression oracle. |
| `README.md` | User-facing docs. |


## Notifications

**`notifier.py` was deleted 2026-08-20** (with ~20 of its tests). Notifications
are now `shared/notify/` - Telegram, Discord, Fi-SMS - configured from
`shared/notifications.json` and gated by the environment's `allow_notifications`
flag, which `shared/notify/channels.py:load_config` enforces by zeroing every
`enabled` key in dev. The sentiment state-transition alert lives in
`services/sentiment_svc/state_alert.py`.


## Common pitfalls

- **Don't compute scores inline in the UI.** Build inputs, call `scoring.<module>.score(...)`, set tk vars from the result. This is what makes tests possible.
- **Don't read tk vars inside scoring modules.** Pass them as explicit parameters.
- **Don't break the bridge schema.** Adding a field is free. Removing or renaming one is a coordinated multi-repo change.
- **Don't write to the legacy bridge path directly** — let `bridge.write_bridge()` handle both writes.
- **Don't add long docstrings or "what this does" comments** — keep code self-explanatory. Comments are for *why*.
