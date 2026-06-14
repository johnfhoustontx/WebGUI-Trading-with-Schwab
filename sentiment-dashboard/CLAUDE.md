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

A tkinter desktop app that computes a daily 0–10 contrarian sentiment
composite from market data, displays it, and publishes it for other apps
(Options Scanner, Blueprint Analyzer) to consume via a JSON bridge file.

## Architecture

```
sentiment_dashboard.py     UI shell (tkinter). Builds payload,
                           updates display, schedules autofetch.
        │
        ▼
scoring/                   Pure functions. No tk imports here.
  ├─ __init__.py           WEIGHTS dict — single source of truth.
  ├─ types.py              ScoreResult dataclass — the contract.
  ├─ vix.py                term, vix1d, slope, complex
  ├─ put_call.py
  ├─ breadth.py
  ├─ rotation.py
  ├─ sector_perf.py
  ├─ credit_pulse.py
  └─ composite.py          blend, velocity, divergence
        │
        ▼
bridge.py                  write_bridge(payload) → JSON to shared/.
        │
        ▼
D:\AI_Based_Analysis\shared\sentiment_bridge.json    (canonical)
SentimentDashboard\sentiment_bridge.json             (legacy mirror)
        │
        ▼
External consumers: Options Scanner regime_filter, Blueprint Analyzer.
```

## Component catalog (6 components, v4.2)

Source of truth: `scoring/__init__.py:WEIGHTS`. Mirror here for reference.

| Component | Weight | Module | Notes |
|---|---|---|---|
| VIX Complex | 20% | `scoring/vix.py:score_complex` | Internal blend of three sub-scores: Term 50%, VIX1D 33%, Slope 17%. Sub-scores still displayed on VIX tab but no longer enter composite directly. |
| Put/Call | 15% | `scoring/put_call.py` | CBOE $CPCE (Equity) primary. |
| Breadth | 20% | `scoring/breadth.py` | NYSE A/D ratio + % above 50 DMA + H/L. |
| Rotation | 15% | `scoring/rotation.py` | Blended day/3d/week cyclical-vs-defensive (40/40/20). |
| Sector Performance | 25% | `scoring/sector_perf.py` | S&P cap-weighted daily move across 11 GICS sectors. Cap weights in `sentiment_dashboard.SP500_SECTOR_WEIGHTS`. |
| Credit Pulse | 5% | `scoring/credit_pulse.py` | HYG/IEI z-score (60%) + HYG vs 50d MA (40%). 60d cache. |

## Trend regime (v4.1)

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
4. **Wire it in the UI**: add to `_component_meta` table in `_build_composite_panel`, call from `calculate_all_scores`, store in `_component_confidence`.
5. **Extend the bridge payload** in `sentiment_dashboard.py:_write_bridge`: add `component_scores.<name>` and `component_confidence.<name>`. Bridge fields are additive-only — never rename or remove.

## Bridge contract

- **Schema version**: `BRIDGE_SCHEMA_VERSION` constant in `bridge.py`. Bump on any non-additive change.
- **Canonical path**: `D:\AI_Based_Analysis\shared\sentiment_bridge.json`. All new consumers MUST read from here.
- **Legacy path**: `SentimentDashboard\sentiment_bridge.json`. Written with `"deprecated_path": true` for back-compat. Will be removed in a future release.
- **Write triggers**: every call to `calculate_all_scores` (manual recalc + autofetch + initial load) and on app close.
- **Known consumers**:
  - `D:\Schwab Test Project\OptionsScanner\regime_filter.py` — reads `composite_score`, `regime`, and (v4.1+) `trend_regime.state`.
  - Blueprint Analyzer (`src/blueprint_scorer.py`) — reads composite_score.
- **Fields are additive-only**. If you need to remove or rename, bump the major schema version and coordinate with consumers.

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
py -3.11 -m pytest SentimentDashboard/tests -q
```

Required green before merging any change under `scoring/` or `bridge.py`.

Current count: 105 tests covering all 6 component modules, composite math (blend, velocity, divergence), bridge round-trip, and the notifier (formatters, throttling, and credential resolution).

The tkinter UI is not unit-tested. Verify UI changes manually by launching `Launch_Dashboard.bat`.

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
| `sentiment_dashboard.py` | UI shell. ~3500 lines after extraction. |
| `bridge.py` | Bridge writer + schema version + paths. |
| `scoring/` | All scoring logic. Pure functions. |
| `tests/` | pytest suite. Includes `fixtures/bridge_v39_snapshot.json` regression oracle. |
| `Launch_Dashboard.bat` | Windows launcher. |
| `README.md` | User-facing docs. Updated when behavior changes. |
| `sentiment_data.json` | Current session snapshot (auto-created). |
| `sentiment_history.json` | 90-day history (auto-created, auto-appended). |
| `sentiment_bridge.json` | Legacy bridge file (deprecated, still written). |
| `credit_cache.json` | 60-day HYG/IEI cache for Credit Pulse. |

## Where things live in `sentiment_dashboard.py` (rough map)

These line numbers shift; use Grep for current locations.

- Constants + WEIGHTS imports: top of file.
- UI build: `_build_*` methods (`_build_main_panels`, `_build_composite_panel`, `_build_history_tab`, etc.).
- Scoring entrypoint: `calculate_all_scores` — calls each scoring module, blends, autosaves.
- Autosave plumbing: `_autosave`, `_append_today_to_history`.
- Fetch scheduling: `_init_schwab`, `_start_auto_refresh`, `_auto_fetch_tick`, `_maybe_initial_fetch`.
- Bridge: `_write_bridge` → delegates to `bridge.write_bridge`.

## Notifications (v4.0)

Optional Discord + Telegram notifier in `notifier.py`. Lazy-initialized
at the end of `SentimentDashboardApp.__init__` and called from
`_autosave()` after `_write_bridge()` with the same payload dict.

**Config (constructor kwargs > env > local file > shared file)**
- Env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`.
- Local file: `config_notifications.py` anywhere on `sys.path` (e.g.
  next to `sentiment_dashboard.py`). Use `config_notifications.example.py`
  as the template.
- Shared file: when no local one is found, the notifier loads
  `D:\Schwab Test Project\OptionsScanner\config_notifications.py`
  by absolute path — both apps share one credential file. Override the
  location via `SentimentNotifier.SHARED_CONFIG_PATH` if OptionsScanner
  lives elsewhere.
- If no creds are configured anywhere, the notifier logs an info line
  at startup and every `post_sentiment` call is a no-op. **That is the
  opt-out.**

**Throttling** (in `notifier.py`, module-level constants
`THROTTLE_SCORE_DELTA = 0.3`, `THROTTLE_MIN_INTERVAL_SEC = 3600`):
- First autosave per session: always posts.
- After that, posts only if any of:
  - composite score moved by ≥ 0.3, OR
  - `bias` string changed, OR
  - `velocity.regime_break` just went True, OR
  - `divergence_flag` just went non-empty, OR
  - ≥ 60 minutes elapsed since the last post.

**Payload shape** is identical to the bridge dict
(`_build_bridge_payload()`); the notifier reads `composite_score`,
`bias`, `position_size_modifier`, `aggregate_confidence`,
`component_scores.{vix_complex,put_call,breadth,rotation,sector_perf,credit_pulse}`,
`component_confidence.*`, `velocity.regime_break`, `divergence_flag`.

**Network**: only `requests` (auto-installed if missing, mirroring the
OptionsScanner pattern). No `discord.py`, no `python-telegram-bot`.
HTTP sends run on daemon threads so they never block the Tk loop.

## Common pitfalls

- **Don't compute scores inline in the UI.** Build inputs, call `scoring.<module>.score(...)`, set tk vars from the result. This is what makes tests possible.
- **Don't read tk vars inside scoring modules.** Pass them as explicit parameters.
- **Don't break the bridge schema.** Adding a field is free. Removing or renaming one is a coordinated multi-repo change.
- **Don't write to the legacy bridge path directly** — let `bridge.write_bridge()` handle both writes.
- **Don't add long docstrings or "what this does" comments** — keep code self-explanatory. Comments are for *why*.
