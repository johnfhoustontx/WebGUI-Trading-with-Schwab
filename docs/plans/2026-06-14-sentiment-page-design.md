# Sentiment page (NiceGUI) — design

**Date:** 2026-06-14
**Status:** approved
**Route:** `/sentiment`

## Goal

Port the complete Sentiment Dashboard to the NiceGUI webgui as the `/sentiment`
page. Four sections: composite gauge + bias, component breakdown, 30-day history
(with velocity + divergence), and trend regime.

## Key finding: the engine is already self-contained

The source tk app (`D:\Trading With Schwab\sentiment-dashboard\sentiment_dashboard.py`,
5448 lines) was **not** copied — but everything the page needs already is:

- `scoring/` package — byte-identical to source (all 6 component modules +
  `composite.py` (blend/velocity/divergence) + `trend_regime.py`).
- `history_backfill.py` — copied, complete. `backfill_history(schwab,
  sector_data, existing_history, days)` runs the **full scoring path** against
  Schwab daily history and returns per-day snapshots:
  composite (`composite.total_score`), `bias`, `size_modifier`,
  `aggregate_confidence`, `component_scores.*`, `component_confidence.*`.
- `Sectors_Industries_ETFs.xlsx` — present locally.
- Proxy `get_daily_history(symbol, months)` and `get_quotes` — present
  (`schwab-proxy/proxy_client.py`).

The **only** engine logic still trapped in the uncopied tk file is the workbook
loader + sector constants, plus thin UI wrappers around `composite.velocity` /
`composite.divergence` / `trend_regime` — all of which we replicate.

## Architecture (reuse-first)

```
proxy.schwab_client ──> backfill_history(...) ──> [~30 daily snapshots]
                                                       │
sectors_ref.load_sectors_data() (the only port) ──────┘
                                                       ▼
   latest snapshot ─> composite gauge + component cards
   full series     ─> 30-day history chart + composite.velocity()
   component scores ─> composite.divergence()
   SPY 12mo closes ─> trend_regime.classify() + commit_state() (replayed)
```

Heavy work runs off-thread via `nicegui.run.io_bound` with a spinner +
`try/except → ui.notify`, mirroring `pages/options/gamma.py`.

## The "today" decision (resolved)

`backfill_history` deliberately **excludes the current session** (`d < today`;
"live capture owns today"). So intraday the page shows the **most-recent
completed session's** composite — the same value the headless task / bridge
publish. The tk app's live-intraday number comes from a separate ~800-line,
deeply tk-coupled quote-fetch path (`_fetch_worker` → `_apply_schwab_data` →
8× `calculate_*_score`).

**Decision (approved):** reuse `backfill_history` for v1. 100% shared/tested
code; live-intraday porting is a documented follow-up. The snapshot is labeled
with its date so the value is never ambiguous.

## Files

### New — port
`sentiment-dashboard/sectors_ref.py` — extract from the tk file, no tkinter:
- `SECTORS_XLSX`, `SP500_SECTOR_WEIGHTS`, `CYCLICAL_SECTORS`,
  `DEFENSIVE_SECTORS`
- `load_sectors_data(xlsx_path=SECTORS_XLSX) -> list[dict]` (rows carry
  `sp_weight`, consumed by `sector_perf`/`rotation`).

### New — page
`webgui/pages/sentiment.py`:
- Pure transforms (unit-tested): `build_history_figure(snapshots) -> dict`
  (Plotly fig dict), `velocity_line(v) -> str`, `divergence_named(snapshot) ->
  list[(name, score)]`, `commit_trend_regime(spy_closes) ->
  (TrendRegimeResult, committed_state, days_in_state)` (replays `classify` +
  `commit_state` over the last sessions for faithful hysteresis),
  `gauge_score(total) -> float` (0–10 → 0–100 for the svg speedometer),
  `bias_color(bias)`.
- Thin `render()` — widgets + autoload timer + ~120s refresh.
- Reuses `pages/options/svg.py` `speedometer_svg`, `gradient_bar_svg`.

### New — tests
`webgui/tests/test_sentiment.py` + `sentiment-dashboard/tests/test_sectors_ref.py`
(TDD the pure transforms with sample snapshot dicts).

### Edits
- `webgui/main.py` — replace the `/sentiment` stub with the real page.
- `webgui/tests/test_shell.py` — keep `/sentiment` in the expected route set.
- root `CLAUDE.md` — mark Sentiment built; bump "Last updated".

## Sections

1. **Composite gauge + bias** — speedometer (0–10 scaled to the svg gauge),
   bias label (Bullish…Bearish) colored, aggregate confidence, position-size
   modifier, snapshot date.
2. **Component breakdown** — cards for VIX Complex / Put-Call / Breadth /
   Rotation / Sector Perf (+ Credit Pulse shown but flagged out-of-composite
   per `scoring.WEIGHTS`), each: score, confidence bar, weight, interp text.
3. **30-day history + velocity/divergence** — Plotly composite line;
   `composite.velocity(prior_scores, today)` → 3d/5d ROC, 20d z, regime-break;
   `composite.divergence(named_scores)` warning line.
4. **Trend regime** — `trend_regime.classify(spy_closes)` + replayed
   `commit_state`; colored state badge (green bull / yellow range / red bear) +
   description + spy_close / SMA50 / SMA200 / slope / drawdown detail +
   days-in-state.

## Refresh / errors

- Autoload: `ui.timer(0.1, load, once=True)` + spinner.
- Auto-refresh: `ui.timer(120, load)`.
- Errors: `try/except → ui.notify`; proxy-down banner handled by `_layout`.
- Weekend/off-hours sparse data is expected, not a bug (sparse history →
  fewer snapshots; velocity/regime degrade gracefully to `—`).

## Out of scope (follow-ups)

- Live-intraday composite (port of the tk quote-fetch path).
- Sector rotation RRG / dual-momentum detail panel (`sector_rotation_assessment.py`).
- Writing the bridge from the webgui (read-only consumer here).
