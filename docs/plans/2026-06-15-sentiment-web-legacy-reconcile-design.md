# Reconcile web Sentiment with the legacy dashboard — design

**Date:** 2026-06-15
**Status:** approved

## Problem

The webgui Sentiment composite (6.14, "Mild Bullish") diverges from the legacy
tk dashboard (7.33, "Bullish"). Diagnosis:

- The web shot is the **backfill / last-completed-session** path (off-hours,
  `is_rth` false); the legacy was a **live Fetch**.
- The backfill path (`history_backfill._score_one_day`) predates v4.3 and uses
  **different scoring methods** for two components:
  - **Put/Call:** backfill uses market `$CPCE` (no data off-hours → 0 / conf 0);
    legacy/live uses **cap-weighted per-sector P/C** from `/chains` (0.860 → 8).
  - **Rotation:** backfill uses blended day/3d/week `compute_rotation` (→5);
    legacy/live uses **dual-momentum** (`compute_dual_momentum` →7).
  - VIX/Breadth/Sector-Perf use the SAME math both ways — their value
    differences are just different dates (06-08 live vs 06-12 close).
- Secondary web bug: off-hours the component table **mixes sources** (Score from
  backfill, Value like "Cyc rank…"/"+1.25%" from the live sector load).
- Cosmetic: component names ("Put/Call" vs "Put/Call (sectors)", "Breadth" vs
  "Market Breadth", "Sector Perf" vs "Sector Performance") and the VIX value
  format ("Term 5 · 1D 0 · Slope 6" vs "T8-1D1-S3").

**Key constraint:** the backfill path *cannot* be upgraded to v4.3 — per-sector
P/C comes from `/chains`, which is point-in-time only (no historical sector P/C
to backfill). So matching the legacy requires driving the headline from the
**live** path (`compute_live`, which already implements v4.3 sector-P/C +
dual-momentum and was verified to produce 6.73/bullish), not from backfill.

## Reconciliation (approved)

### 1. Headline always uses `compute_live`
Drop the `is_rth` gate that currently restricts live compute to market hours, in
**both** the page `load()` and the background `_refresh_cache_sync`. Always call
`_load_live()` / `compute_live(...)`; use the live snapshot as the headline
(gauge / component table / tiles / bridge). Fall back to the backfill snapshot
only when `compute_live` returns nothing (total failure). Backfill remains the
source for the **30-day history chart** only.

This fixes Put/Call (0→sector-P/C), Rotation (blended→dual-momentum), and the
mixed-source component-table inconsistency in one move — matching the legacy
methodology around the clock.

`is_rth` is kept, but only for the **date-label wording**: live + RTH →
"live intraday"; live + off-hours → "latest (market closed)"; backfill fallback →
"last completed session". Off-hours the live values reflect last-session quotes
(breadth may read 0 / low-conf, exactly as the legacy "Fetch Live" does
off-hours) — accepted trade-off.

### 2. Align labels / value format
- `COMPONENTS` display names → legacy: "Put/Call (sectors)", "Market Breadth",
  "Sector Performance" (VIX Complex / Rotation / Credit Pulse unchanged). Bridge
  keys are unchanged (keyed by the internal key, not the display name).
- VIX value in `compute_live` → `T{term}-1D{vix1d}-S{slope}` (sub-scores), matching
  the legacy `T8-1D1-S3` format, instead of the score module's prose interp.

## Out of scope
- Upgrading the backfill path (infeasible: no historical sector P/C).
- The 30-day history chart (stays backfill — it's a long series, not a single
  composite; methodology drift there is immaterial to the trend shape).
- Breadth value-string format ("29.17:1") — the interp ("A/D 1.89:1 — …") is
  retained as more informative; not part of this reconciliation.

## Testing
- Update unit tests that assert the old component display names.
- `is_rth` stays (used for label wording) — its test stays.
- Script-verify `compute_live` reproduces the legacy methodology (sector-P/C
  put_call, dual-momentum rotation) against the live proxy.
