# Reconcile web Sentiment with legacy — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans.

**Goal:** Web headline always uses the live v4.3 path (sector-P/C + dual-momentum), matching the legacy; align component labels + VIX value format. Backfill stays only for the 30-day history chart.

**Design:** [`2026-06-15-sentiment-web-legacy-reconcile-design.md`](2026-06-15-sentiment-web-legacy-reconcile-design.md)

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (venv `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`).

---

## Task 1 — Always-live headline (drop `is_rth` fetch gate)

**`webgui/pages/sentiment.py`:**
1. Background refresher (`_refresh_cache_sync`, ~line 578):
   `live = _load_live() if is_rth(datetime.now(ZoneInfo("America/Chicago"))) else None`
   → `live = _load_live()`
2. Page `load()` (~lines 975-982): replace the `if is_rth(...): live = await io_bound(_load_live) else: live = None` block with:
   `live = await ng_run.io_bound(_load_live)`
   (keep `state["live"]=live` / `_CACHE["live"]=live`). Leave `from datetime import datetime` for `composite_at`; drop the now-unused `ZoneInfo` import in `load` if it lints.
3. Date label in `_apply` (~lines 755-756): make it three-way using `is_rth`:
   ```python
   from datetime import datetime
   from zoneinfo import ZoneInfo
   if live:
       rth = is_rth(datetime.now(ZoneInfo("America/Chicago")))
       date_lbl.text = (f"as of {latest.get('date')} (live intraday)" if rth
                        else f"as of {latest.get('date')} (latest — market closed)")
   else:
       date_lbl.text = f"as of {latest.get('date')} (last completed session)"
   ```
`_apply` already does `latest = live or snaps[-1]`; `_load_live` returns None on failure → backfill fallback preserved. `is_rth` + its test stay (now used only for label wording).

**Verify:** `pytest -q` green; `import main` ok.
**Commit:** `feat(sentiment): web headline always uses live v4.3 composite (match legacy); backfill only for 30d history`

---

## Task 2 — Align labels + VIX value format

1. **`webgui/pages/sentiment.py` `COMPONENTS`** (~lines 37-44): rename display names to match the legacy:
   - `("put_call", "Put/Call (sectors)", ...)`
   - `("breadth", "Market Breadth", ...)`
   - `("sector_perf", "Sector Performance", ...)`
   (VIX Complex / Rotation / Credit Pulse unchanged.)
2. **`sentiment-dashboard/live_composite.py`** (~line 286): VIX value → sub-score format:
   `"volatility": {"interpretation": f"T{int(term.score)}-1D{int(v1d_r.score)}-S{int(slope.score)}"},`
   (term / v1d_r / slope ScoreResults exist at lines 209-211.)
3. **Update tests for the renamed display names:**
   - `webgui/tests/test_sentiment.py::test_divergence_named_extracts_confident_components`: `"Sector Perf"` → `"Sector Performance"` (keep "VIX Complex").
   - `webgui/tests/test_sentiment_sectors.py::test_component_table_rows_contrib`: `by["Put/Call"]` → `by["Put/Call (sectors)"]`, `by["Sector Perf"]` → `by["Sector Performance"]`.
   - Grep both test files for the old display strings and fix any other assertions.

**Verify:** `pytest -q` green; `import main` ok.
**Commit:** `feat(sentiment): align component labels (Put/Call (sectors)/Market Breadth/Sector Performance) + VIX T-1D-S value format with legacy`

---

## Task 3 — Verify + docs

1. **Script-verify** (temp, deleted): run `live_composite.compute_live(proxy.schwab_client, sectors_ref.load_sectors_data())` and print component_scores + composite; confirm put_call uses sector P/C (non-zero when chains return) and rotation = dual-momentum score (matches the legacy method). (Off-hours breadth may be 0 — expected.)
2. **Docs:** root `CLAUDE.md` sentiment note — state the headline always uses the live composite (sector-P/C + dual-momentum), backfill only for the 30d history; component labels match the legacy. Bump test count if changed.
3. **Commit** docs.

---

## Gotchas
- Bridge keys are the internal component keys (`put_call`, `sector_perf`), NOT the display names — renaming display names doesn't change the bridge schema; only the `divergence_flag` text (now "Put/Call (sectors) X vs …", matching legacy).
- Off-hours: live breadth ($ADVN/$DECN) reads 0/low-conf — same as legacy "Fetch Live" off-hours (accepted).
- Don't touch backfill (`_score_one_day`) — it can't compute historical sector P/C; it's only the 30d-history source now.
- No `from scoring import` in functions; keep imports at module top.
