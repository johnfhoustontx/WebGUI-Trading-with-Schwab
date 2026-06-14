# Sentiment page — complete to mirror source (sector perf + layout) — design

**Date:** 2026-06-14
**Status:** approved
**Builds on:** [`2026-06-14-sentiment-page-design.md`](2026-06-14-sentiment-page-design.md)

## Goal

Bring the NiceGUI `/sentiment` page to parity with the source tk dashboard
(`D:\Trading With Schwab\sentiment-dashboard\sentiment_dashboard.py`):

1. **Sector & Industry Performance section** — 11-sector table (Day/Week/Month %,
   P/C Ratio, RRG quadrant), rotation banner, and "% green | Cap-wtd | Score"
   summary. *Industry expandable sub-rows deferred to an agreed fast-follow.*
2. **Component panel reformat** — Component · Value · Score · Weight · Conf ·
   Contrib table (currently cards).
3. **5 summary tiles** — MODIFIER · BIAS · SIGNAL · YESTERDAY · CHANGE.
4. **Rolling averages** — 5d/20d + Rising/Falling/Stable under the history chart.

## Architecture (reuse-first)

Nearly all computation already exists in copied pure modules — no new engine math:
- `scoring/rotation.py`: `compute_rotation(sector_data, sector_trends, quotes)`,
  `compute_dual_momentum(sector_history, sp_weights, irx, lookback_days=63)`,
  `compute_rrg_quadrants(sector_history, spy_closes, rs_window=50, mom_window=20)`,
  `CYCLICAL_SECTORS`/`DEFENSIVE_SECTORS`.
- `scoring/sector_perf.py`: `weighted_sector_pct`, `sectors_score`.
- `sectors_ref.py`: `load_sectors_data`, `SP500_SECTOR_WEIGHTS`.

**Only new impure code:** a P/C-ratio fetch ported verbatim from source
`sentiment_dashboard.py:2919-2955` — split into a pure parser
`pcr_from_chain(chain) -> float|None` (unit-tested) + a thin fetch loop in the
data-loader.

## New data load: `_load_sector_perf(spy_closes)` (off-thread)

| Data | Call(s) | Use |
|------|---------|-----|
| `get_quotes(11 sector ETFs)` | 1 | Day % (`change_pct`) |
| `get_daily_history(etf, months=3)` ×11 | 11 | Week % (n=5), Month % (n=21), 3d % (n=3), RRG close history |
| SPY 12-mo closes | 0 | **reused** from the composite load's `state["spy"]` |
| `get_quote("$IRX")` | 1 | dual-momentum cash hurdle |
| `pcr_from_chain` over 11 `/chains` | 11 | per-sector P/C |

**~24 calls.** Returns `{quotes, trends, closes, pcr, quadrants, dual, irx}`.
**Refresh strategy:** initial page load and the Refresh button load composite +
sectors; the 120 s auto-timer refreshes the **composite only** (sectors are
manual) to bound `/chains` volume — matching the source's separate Refresh button.

## Section 1 — Sector & Industry Performance

`ui.table`, 11 sector rows sorted by Day % desc, columns:
Sector · ETF · Description · Day % · Week % · Month % · P/C · RRG.
Per-cell color via a body-cell slot:
- pct: `>0` green, `<0` red, `|pct|<0.05` flat (`pct_color`).
- P/C: `<0.95` green, `>1.05` red, else flat (`pcr_color`); blank when None.
- RRG: Leading=green, Improving=cyan `#3fb6c7`, Weakening=yellow, Lagging=red
  (`rrg_color`); blank when unknown.

**Rotation banner** (above table) from `compute_rotation` →
`rotation_banner(rot)`: regime label + color (≥1.0 STRONG RISK-ON, ≥0.3 RISK-ON,
≤-1.0 STRONG RISK-OFF, ≤-0.3 RISK-OFF, else MIXED) + detail
`"{TF}: Cyc {x} vs Def {y} (spread {z})  ▲ {top2}  ▼ {bot2}"` with day→3d→week
timeframe fallback (mirrors source `_update_rotation_banner`).

**Summary line** `sector_summary(sector_data, quotes)`:
`"{pct_up:.0f}% green | Cap-wtd {wpct:+.2f}% | Score {sectors_score:.1f}/10"`.

## Section 2 — Component table

Replace the component cards with a table: **Component · Value · Score · Weight ·
Conf · Contrib**. `Contrib = weight · score · conf`; the composite already equals
`Σcontrib / Σ(w·c)`. Scores/confidences come from the **snapshot** (so Contrib
reconciles to the displayed composite). Value per component:
- vix_complex → snapshot `volatility.interpretation`
- put_call → snapshot `options.pc_equity`
- breadth → snapshot `breadth.interpretation`
- rotation → `compute_dual_momentum(...)['interp']` (the "Cyc rank … rotation"
  string) when sector data is loaded, else snapshot `rotation.interpretation`.
  *Note:* the Score column shows the snapshot's `compute_rotation`-based score
  (keeps Contrib consistent); the Value is the rank-based descriptor — a small,
  documented divergence from the source which uses dual-momentum for both.
- sector_perf → cap-wtd `"{wpct:+.2f}%"` from the loaded sector quotes.

Keep the gauge above; keep the inline Market Trend row + velocity/divergence.

## Section 3 — 5 tiles

`tiles(latest, prev)` from the score banding in source `_update_position_modifier`:
≥9 → 1.25x/Long/Strong Bull; ≥7 → 1.10x/Long/Bullish; ≥5 → 1.00x/Neutral/Neutral;
≥3 → 0.85x/Cautious/Bearish; else 0.70x/Short/Strong Bear. MODIFIER=size,
BIAS=bias, SIGNAL=signal, YESTERDAY=prev composite, CHANGE=today−prev (+/−, colored).

## Section 4 — Rolling averages

`rolling_averages(prior_scores)` → `(a5, a20, label)`; label Rising if
`a5 > a20 + 0.3`, Falling if `a5 < a20 − 0.3`, else Stable (mirrors source
`_build_bridge_payload` momentum). Shown under the history chart.

## Pure transforms (TDD)

`pcr_from_chain`, `week_month_from_closes` (returns 3d/week/month % via n=3/5/21),
`pct_color`, `pcr_color`, `rrg_color`, `sector_table_rows`, `sector_summary`,
`rotation_banner`, `component_table_rows`, `tiles`, `rolling_averages`.
`render()` stays thin; `_load_sector_perf` is the only new I/O.

## Testing / verify

Unit tests for every transform. Browser-verify the full page (restart preview):
screenshot the sector table + banner + tiles + component table + rolling avgs.
Weekend/off-hours sparse data (missing `/chains`, thin history) degrades to
blank cells / "—", not errors.

## Out of scope (fast-follow)

- Expandable **industry** sub-rows (lazy per-sector fetch + tree widget).
- Live-intraday composite (still last-completed-session, per the base design).
