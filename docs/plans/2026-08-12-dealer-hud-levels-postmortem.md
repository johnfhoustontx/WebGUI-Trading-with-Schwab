# Dealer-positioning levels: postmortem and fixes

**Date:** 2026-08-12
**Components:** `Indicators/JFIndicators/NQDealerPositioning.cs` (NinjaTrader),
`tools/nq_hud.py`, `options-scanner/gamma_tool.py`

Presenting symptom, reported over several sessions: *"the call wall is displayed
but the put wall and gamma flip are not."* It took three wrong diagnoses to find
the cause. This records what actually happened, because the way it was missed is
more reusable than the bug.

---

## 1. What was actually wrong

All four levels were drawn inside **one** `try/catch`, in `OnRefreshTick`:

```
CALLWALL line   -> Draw.HorizontalLine  -> OK   (anchors to a PRICE)
CALLWALL label  -> Draw.Text            -> THREW (anchors to a BAR)
                -> exception unwinds the whole sequence
                -> swallowed by catch { }
FLIP / PUTWALL / PIN -> never attempted
```

`Draw.Text` resolves `barsAgo` against `CurrentBar`, and the anchor was
invalid:

```
'barsAgo' needed to be between 0 and 5024 but was 6
```

**Corrected 2026-08-12 (see §6):** this was first attributed to a stale bar
series. That was wrong — the chart's data was current. `Close[0]` / `Time[0]` /
`CurrentBar` resolve against the indicator's internal bar POINTER, which is only
meaningful inside `OnBarUpdate`; read from the render or timer thread they can
point near the START of the loaded series. Same root cause, different subject:
the bar pointer, not the data.

The first level's *caption* was killing every level behind it.

### The tell that was on screen the whole time

**The call wall line had no label.** That says "drawing failure", not "scaling
failure". Two releases were spent on scaling.

### The disproof that was available immediately

Put wall 29,826 and pin 29,821 sat **inside** the visible price range
(~29,730–30,030) and still were not drawn. No scaling theory survives that. The
objects were never being *created*.

---

## 2. Why it was misdiagnosed three times

| Release | Theory | Why it was wrong |
|---|---|---|
| 2.1.1 | Levels excluded from the price scale | The flag was already ON |
| 2.2.0 | Null flip + tail-strike put wall | True of a **stale dev state file** the indicator was wrongly pointed at, not of live data |
| 2.2.1 | `IsOverlay=false` blocks autoscale | Real NinjaTrader behaviour, but not the cause |

Every one of those was reasoned from source code and config files. **None was
checked against the running chart.** A single screenshot settled it.

**Rules adopted:**

1. Before theorising about why something is not *visible*, prove whether it was
   *created*.
2. Never wrap a sequence of independent operations in one `catch`. One failure
   silently cancels the remainder and looks like a different bug entirely.
3. Never swallow an exception in a rendering path. A silent failure to draw is
   indistinguishable from a request that was never made.
4. Verify by observing the running system, not by re-reading the code.

---

## 3. Secondary defects found along the way

Each was real, and each could have put a line at a wrong price.

### 3.1 Wrong state file (fixed, 2.2.0)
The indicator defaulted to the **dev** tree's `nq_state.json`, two days stale,
while `nq_hud.py` runs from **Prod**. Insidious because rebasing pins levels to
current price, so a two-day-old call wall still landed within three points of
spot and looked credible. Only the levels furthest from spot visibly broke.

### 3.2 `Close[0]` made levels bar-interval dependent (fixed, 2.2.2)
`Close[0]` is the last *bar's* close, so the rebasing inherited the chart's
interval: 1-minute and 5-minute charts computed different levels, and
`Calculate=OnBarClose` made it a full bar worse. Now uses
`Instrument.MarketData.Last.Price`.

### 3.3 Rebasing through `cash_spot` (fixed, 2.3.1)
Two defects in `level = cash_level + (chart_price − cash_spot)`:

- `cash_spot` **freezes** when the cash index closes while futures trade
  overnight. Measured pre-open: chart basis `396.52` vs the exporter's `301.27`
  — every level **95 points wrong**, silently.
- `chart_price` ticks continuously while `cash_spot` updates every 2s, so
  between reads `d(level)/d(price) = 1`. In a fast move the walls slid with
  price and snapped back.

Replaced by one formula that degenerates correctly in every case:

```
level_on_chart = fut_level + chart_offset
```

`fut_level` is `cash_level` plus the exporter's basis — a **matched pair**, both
sides sampled at the same instant. `chart_offset` is this chart's price minus
that same futures price, sampled once per read and median-filtered. It is ~0 on
the exporter's own contract, the calendar spread on a different expiry, and
spread plus adjustment on a back-adjusted continuous chart.

### 3.4 Basis noise made static strikes shimmer (fixed, 2.3.0 + this change)
Measured live over 21 seconds:

```
cash_call_wall  29810.00  29810.00  29810.00  29810.00   <- static
fut_call_wall   29912.82  29913.57  29912.41  29913.12   <- wandering
basis             102.82    103.57    102.41    103.12   <- the culprit
```

The strike never moved. `fut_level = cash_level + basis`, and the basis is
measured as an **instantaneous** futures-minus-cash. Those two quotes tick
independently — the future's bid/ask bounce against the index's recalculation
lag — so ~0.6 points of pure measurement noise lands on every level. The real
basis is carry plus dividends and drifts over hours.

Now median-filtered in **both** places: the indicator (chart) and
`BasisSmoother` in `nq_hud.py` (source, so the HUD window benefits too). Median
rather than mean so one crossed quote moves nothing; a jump beyond a threshold
clears the history so a genuine strike change or contract roll is adopted
immediately. It is a noise filter, not a lag.

### 3.5 Wall picker returned tail strikes (fixed, this change)
`get_directional_walls` took the extreme strike on each side with no proximity
constraint. On a thin or stale grid that produced a **put wall of 14,000 against
a spot of 29,722** — a deep tail strike carrying the largest put entry once the
near-the-money rows dropped out.

Added an optional `max_pct` bound; `nq_hud.py` passes 10.0. Default `None`
preserves behaviour for every existing caller (`scanner_engine`, `compute.py`,
`gamma_window_legacy`).

---

## 4. Changes made

### `NQDealerPositioning.cs` — 2.1.1 → 2.3.1
- Level classification: `usable` / `not published` / `off-scale`, shared by the
  readout and the chart lines so they can never disagree
- Default state path → Prod
- `IsOverlay = true` (a drawn level can only expand the axis if its owner shares
  that axis) — **requires remove-and-re-add**, it is baked into saved instances
- Chart price from the live tape, not `Close[0]`
- **Per-level and per-label `try/catch`**; all failures surfaced, none swallowed
- Level captions **painted** (SharpDX) rather than drawn — no bar anchor, so the
  failure mode above cannot recur
- Median filter on the futures levels and the offset
- Self-diagnosis: warns on `IsOverlay=false`, `Calculate=OnBarClose`, and a
  chart on a different contract than the exporter quoted

### `tools/nq_hud.py`
- `BasisSmoother` — per-instrument median filter with jump reset
- `build_pane(..., basis=None)` — stays pure; the poller owns the history
- Wall picker bounded by `WALL_MAX_PCT = 10.0`

### `options-scanner/gamma_tool.py`
- `get_directional_walls(..., max_pct=None)` — backward compatible

### Tests
10 new (`test_basis_smoother.py`, 4 added to `test_directional_walls.py`).
518 pass across every consumer of the changed functions. The 2 remaining
failures (`check_earnings_conflict`, hardcoded May-2026 dates) fail identically
in the untouched Prod checkout and are unrelated.

---

## 5. Still open

- ~~Bar series stale on the NQ chart~~ — **retracted, see §6.** The data was
  current; the diagnostic was wrong.
- **Prod promote pending** for the `nq_hud.py` and `gamma_tool.py` changes: they
  are staged in dev and require merge to `main` plus `promote.bat`, which
  restarts the stack.
- `pick_flip` / `calc_flip_point` were never audited. The flip is interpolated
  rather than a strike, so it legitimately moves more than the walls — but its
  noise characteristics are unknown.


---

## 6. Retraction: the "stale bar series" that never was

`NQDealerPositioning` 2.2.3 added a diagnostic that reported:

```
Close[0] 29500.00   bar 08-06   STALE 135h
```

on a chart that was displaying current bars. It was taken at face value, used
to explain the `Draw.Text` failure, and written into §1 of this document as
fact. It was false.

The data was fine — `db/minute/NQ 09-26/20260812.Last.ncd` existed, with no gap
after Aug 6.

**What actually happened:** `Close[0]` and `Time[0]` resolve against the
indicator's internal bar pointer. That pointer is only meaningful inside
`OnBarUpdate`. This indicator reads nothing from `OnBarUpdate` — everything
happens on a dispatcher timer and in `OnRender` — and from there the indexers
pointed near the *start* of the loaded series. On a chart holding ~5,000
one-minute bars that is a timestamp several days old. Comparing it to the wall
clock manufactured a stale-data alarm out of nothing.

Fixed in 2.3.2: the row now reports `Bars.GetClose` / `Bars.GetTime` at absolute
index `Bars.Count - 1`, which does not depend on the pointer, and raises no
alarm.

**The lesson, which is the sharpest one here:** a diagnostic that can be wrong
is worse than no diagnostic, because it is believed. This one was added *while
fixing* a chain of misdiagnoses and promptly caused another. Instrumentation
needs the same scepticism as the code it measures — and, like the code, it
should be checked against ground truth before its output is trusted.

It also retroactively strengthens two earlier decisions: dropping `Close[0]` for
`Instrument.MarketData.Last.Price` (2.2.2) and removing the bar-anchored label
entirely (2.3.1). Both removed dependencies on a value that was never reliable
in this indicator's execution context.
