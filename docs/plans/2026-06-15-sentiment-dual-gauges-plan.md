# Dual sentiment/trend speedometers — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a second speedometer to each of the two top gauges on `/sentiment` — **Sentiment: Today + 30-Day Avg** and **Market Trend: Today + ~30-sessions-ago** — reusing the existing gauge anchors.

**Architecture:** 3-tier. The page is a thin renderer reading the Redis bus; scoring lives in `services/sentiment_svc`. The page already renders a sentiment speedometer (`gauge_score`) and a trend speedometer (`trend_gauge_value`). This adds (a) a page-side 30-day **average** sentiment gauge (pure arithmetic over `state["snaps"]`) and (b) a 30-day-**ago** trend gauge fed by a new `derived["trend_30d_ago"]` the **service** publishes (the page can't classify SPY 30 bars back — no scoring engine).

**Tech Stack:** NiceGUI (`speedometer_svg`, `ui.html`/`ui.row`), pytest. Service: `scoring.trend_regime` (already imported in `sentiment_svc/compute.py`).

**Design:** [`2026-06-15-sentiment-dual-gauges-design.md`](2026-06-15-sentiment-dual-gauges-design.md)

**Tests:** webgui — `cd webgui && ..\.venv\Scripts\python -m pytest -q`; service — from repo root `.venv\Scripts\python -m pytest services\sentiment_svc -q`. venv: `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`.

**Key existing code (read first):**
- `webgui/pages/sentiment.py`: `gauge_score(total)`, `composite_series(snaps)` (pure, line ~107), `trend_gauge_value(trend)` (0–100 hybrid anchor, ~84), `_TREND_SHORT` (~80); the top region `ui.row` with column ① (sentiment: `gauge_box` ~482) and column ② (trend: `trend_gauge_box` ~495); `_apply` (~579) sets `gauge_box.content`/`trend_gauge_box.content`; `state["snaps"]`, `state["derived"]`.
- `services/sentiment_svc/compute.py`: `build_trend_dict(spy)` (~261, returns {state,label,description,sma_200_slope_pct,drawdown_pct,…}), `derive_composite_extras(live, snaps, spy)` (~299, returns the `derived` dict ending at ~348-356 with `"trend": build_trend_dict(spy)`), `trend_regime.MIN_BARS_PARTIAL`.
- `services/sentiment_svc/handlers.py`: publishes `derive_composite_extras(...)` under `cache:sentiment:composite["derived"]`.

---

## Task 1 (Tier-2): publish `derived["trend_30d_ago"]`

**Files:**
- Modify: `services/sentiment_svc/compute.py` (`derive_composite_extras` return)
- Test: `services/sentiment_svc/tests/test_compute.py` (add)

**Step 1: Write the failing test** (append to the compute test file; match its import style — it imports `compute`):

```python
def test_derive_composite_extras_includes_trend_30d_ago():
    # 260 rising closes -> a valid regime; [:-30] still has >200 bars.
    spy = [100.0 + i * 0.5 for i in range(260)]
    snaps = [{"composite": {"total_score": "6.00"}}]
    out = compute.derive_composite_extras(live=None, snaps=snaps, spy=spy)
    assert "trend" in out and "trend_30d_ago" in out
    t30 = out["trend_30d_ago"]
    assert t30 is not None and t30.get("state") in {
        "bull_trend", "pullback_in_bull", "range", "bear_rally", "bear_trend"}
    # carries the fields the gauge nudge needs
    assert "sma_200_slope_pct" in t30 and "drawdown_pct" in t30


def test_derive_composite_extras_trend_30d_ago_degrades_on_short_spy():
    spy = [100.0 + i for i in range(40)]  # < 30 + MIN_BARS_PARTIAL -> use full spy
    out = compute.derive_composite_extras(live=None, snaps=[], spy=spy)
    assert "trend_30d_ago" in out   # present (may equal today's or None), never raises
```

**Step 2: Run — expect FAIL** (`trend_30d_ago` missing).
Run: `.venv\Scripts\python -m pytest services\sentiment_svc\tests\test_compute.py -q`

**Step 3: Implement.** In `derive_composite_extras`, before the `return {...}`, add:

```python
    # ~30-sessions-ago regime for the Market Trend "30d ago" gauge. The webgui
    # can't classify (no scoring engine), so compute + publish it here. Degrade
    # to the current regime when there isn't enough pre-window history.
    back = 30
    spy_30 = spy[:-back] if (spy and len(spy) > back + trend_regime.MIN_BARS_PARTIAL) else spy
    trend_30d_ago = build_trend_dict(spy_30)
```

and add `"trend_30d_ago": trend_30d_ago,` to the returned dict (next to `"trend"`).

**Step 4: Run — expect PASS.** Then the full service suite:
`.venv\Scripts\python -m pytest services\sentiment_svc -q` (report count; existing tests stay green).

**Step 5: Commit**
```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/tests/test_compute.py
git commit -m "feat(sentiment_svc): publish derived.trend_30d_ago for the 30d-ago trend gauge"
```

---

## Task 2 (Tier-1): `sentiment_30d_avg` helper + two new gauges

**Files:**
- Modify: `webgui/pages/sentiment.py` (new pure helper; top-region layout; `_apply`)
- Test: `webgui/tests/test_sentiment.py` (add)

**Step 1: Failing test** for the pure helper (append):

```python
def test_sentiment_30d_avg():
    from pages import sentiment as S
    snaps = [{"composite": {"total_score": "6.0"}},
             {"composite": {"total_score": "0.0"}},   # zero filtered out
             {"composite": {"total_score": "8.0"}}]
    assert S.sentiment_30d_avg(snaps) == 7.0          # mean(6,8)
    assert S.sentiment_30d_avg([]) == 0.0
```

**Step 2: Run — expect FAIL.**
`cd webgui && ..\.venv\Scripts\python -m pytest tests/test_sentiment.py -q`

**Step 3: Implement the helper** (module level, near `composite_series`):

```python
def sentiment_30d_avg(snaps):
    """Mean composite over the backfill history (0.0 if none). Pure."""
    scores = composite_series(snaps or [])[1]
    return round(sum(scores) / len(scores), 2) if scores else 0.0
```

**Step 4: Add the two gauges in the top-region layout.**
- In column ① (Market Sentiment), wrap the existing `gauge_box` in a 2-gauge row with captions and add the avg gauge:
  ```python
          with ui.column().classes("items-center").style("min-width:210px"):
              ui.label("Market Sentiment").classes("text-h6")
              with ui.row().classes("items-end justify-center gap-4 no-wrap"):
                  with ui.column().classes("items-center"):
                      ui.label("Today").classes("opacity-60 text-xs")
                      gauge_box = ui.html("").classes("q-mt-xs")
                  with ui.column().classes("items-center"):
                      ui.label("30-Day Avg").classes("opacity-60 text-xs")
                      gauge_avg_box = ui.html("").classes("q-mt-xs")
              bias_lbl = ui.label("").classes("text-h6")
              ...
  ```
- In column ② (Market Trend), do the same for the trend pair:
  ```python
          with ui.column().classes("items-center").style("min-width:210px"):
              ui.label("Market Trend").classes("text-h6")
              with ui.row().classes("items-end justify-center gap-4 no-wrap"):
                  with ui.column().classes("items-center"):
                      ui.label("Today").classes("opacity-60 text-xs")
                      trend_gauge_box = ui.html("").classes("q-mt-xs")
                  with ui.column().classes("items-center"):
                      ui.label("~30d Ago").classes("opacity-60 text-xs")
                      trend_gauge_30_box = ui.html("").classes("q-mt-xs")
              regime_badge = ui.label("").classes("text-subtitle1 text-bold")
              ...
  ```
  (Keep the existing bias/sub labels, Components/Trend-Detail popups, and `regime_badge`/`regime_desc` exactly as they were — only the gauge area is wrapped into a pair.)

**Step 5: Set the new gauges in `_apply`.** Use smaller dimensions for the gauge pairs (e.g. width=150, height=100) for both members of each pair so they fit side by side; keep the grade labels:
- Sentiment today (existing `gauge_box.content = speedometer_svg(gauge_score(total), comp.get("bias",""), …)`) → set `width=150, height=100`.
- Sentiment avg: `avg = sentiment_30d_avg(state["snaps"]); gauge_avg_box.content = speedometer_svg(gauge_score(avg), f"{avg:.2f}", width=150, height=100)`.
- Trend today (existing `trend_gauge_box.content = speedometer_svg(trend_gauge_value(trend), _TREND_SHORT.get(trend.get("state"), "—"), …)`) → `width=150, height=100`. (Match the existing call's grade arg.)
- Trend 30d-ago: `t30 = (state["derived"] or {}).get("trend_30d_ago"); trend_gauge_30_box.content = speedometer_svg(trend_gauge_value(t30), _TREND_SHORT.get((t30 or {}).get("state"), "—"), width=150, height=100)`.
  Handle the empty/None case (the existing code already shows a `50.0, "—"` fallback for the today gauge — mirror it for the 30d gauge when `t30` is falsy).

Read the existing `_apply` gauge lines (~597 sentiment, ~634/648 trend) and mirror their exact `speedometer_svg(...)` argument shape (grade label) for the new gauges.

**Step 6: Run** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (helper test + shell green); import smoke `..\.venv\Scripts\python -c "import sys; sys.path.insert(0,'.'); sys.path.insert(0,'..'); import main; print('ok')"`.

**Step 7: Commit**
```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment.py
git commit -m "feat(sentiment): dual speedometers — Sentiment Today+30d-avg, Trend Today+~30d-ago"
```

---

## Task 3: Verify + docs

1. **Service script/test** confirms `derived["trend_30d_ago"]` is published (Task 1 test covers it). Optionally run `sentiment_svc` once against the live proxy to eyeball that today vs 30d-ago regimes differ when the trend shifted.
2. **Browser** (when port 8500 frees): restart the webgui preview, open `/sentiment`, confirm 4 gauges (Sentiment Today / 30-Day Avg; Trend Today / ~30d Ago) render with sensible needles + captions. a11y snapshot fallback if the renderer is slow. (Requires `sentiment_svc` running + Memurai up so the bus has data; otherwise the page shows the waiting placeholder.)
3. **Docs:** root `CLAUDE.md` `/sentiment` note — mention the dual Today/30-day gauges for both Sentiment and Trend; the 30d-ago trend comes from `derived["trend_30d_ago"]`. Bump test counts.
4. **Commit** docs.

---

## Gotchas
- **Tier discipline:** the page must NOT import scoring — the 30d-ago regime is computed in `sentiment_svc` and read from `derived`. The 30-day *average* is pure arithmetic over `snaps` (page-side `composite_series`), so it stays on the page.
- **Reuse the existing anchors:** `gauge_score` (sentiment 0→0-100) and `trend_gauge_value` (trend regime+slope/dd → 0-100). Do NOT introduce a separate 0–10 trend map — the today-trend gauge already uses `trend_gauge_value`, and both trend gauges must match.
- `build_trend_dict(spy[:-30])` returns the same shape as today's trend (incl. `sma_200_slope_pct`/`drawdown_pct`), so `trend_gauge_value` works on it unchanged.
- Smaller gauge dimensions (≈150×100) so each pair fits in the ~210px column; keep `flex-wrap` on the outer row so the two columns wrap on narrow viewports.
- `derived` is a loose dict — adding `trend_30d_ago` needs no contract change (verify `shared/contracts` doesn't pin it; it doesn't today).
- Running the page needs Memurai + `sentiment_svc` up (3-tier); without them the page renders the waiting placeholder — not a bug.
