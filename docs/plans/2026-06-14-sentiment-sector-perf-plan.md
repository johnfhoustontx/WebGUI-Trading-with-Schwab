# Sentiment page — sector perf + layout parity — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the Sector & Industry Performance section (11 sectors) + reformat the component panel into a table + add the 5 summary tiles + 5d/20d rolling averages to `/sentiment`, reusing existing pure engines.

**Architecture:** New pure transforms in `webgui/pages/sentiment.py` (TDD'd) + one new off-thread loader `_load_sector_perf` that calls the proxy and the copied `scoring.rotation`/`scoring.sector_perf` functions. Only new impure logic is a P/C-chain fetch (pure parser + thin loop). `render()` stays thin.

**Tech Stack:** NiceGUI (`ui.table` with body-cell color slot, `ui.row`/`ui.grid`, `nicegui.run.io_bound`), pytest. Reuses `scoring/rotation.py`, `scoring/sector_perf.py`, `sectors_ref.py`.

**Design:** [`2026-06-14-sentiment-sector-perf-design.md`](2026-06-14-sentiment-sector-perf-design.md)

**Run tests:** from `D:\WebGUI Trading with Schwab\webgui` (PowerShell): `..\.venv\Scripts\python -m pytest -q`. venv python: `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`.

**Reference (read-only):** source formats live in `D:\Trading With Schwab\sentiment-dashboard\sentiment_dashboard.py` — `_apply_sector_perf` (2198), `_update_rotation_banner` (1737), `_update_position_modifier` (4006), P/C fetch (2919-2955); rotation/dual-momentum/RRG in the **copied** `sentiment-dashboard/scoring/rotation.py`.

---

## Task 1: Color + math pure helpers + P/C parser

**Files:**
- Modify: `webgui/pages/sentiment.py` (append pure helpers after the existing transforms; do NOT touch existing functions or `render()`)
- Test: `webgui/tests/test_sentiment_sectors.py` (new)

**Step 1: Write failing tests** — create `webgui/tests/test_sentiment_sectors.py`:

```python
"""Pure-transform tests for the Sentiment sector-perf additions."""
from pages import sentiment as S


def test_pct_color_buckets():
    assert S.pct_color(0.5) == S.CLR_GREEN
    assert S.pct_color(-0.5) == S.CLR_RED
    assert S.pct_color(0.01) == S.CLR_FLAT      # |pct| < 0.05 -> flat
    assert S.pct_color(None) == S.CLR_FLAT


def test_pcr_color_buckets():
    assert S.pcr_color(0.80) == S.CLR_GREEN     # call-dominated
    assert S.pcr_color(1.20) == S.CLR_RED       # put-dominated
    assert S.pcr_color(1.00) == S.CLR_FLAT      # neutral band
    assert S.pcr_color(None) == S.CLR_FLAT


def test_rrg_color_map():
    assert S.rrg_color("Leading") == S.CLR_GREEN
    assert S.rrg_color("Improving") == S.CLR_CYAN
    assert S.rrg_color("Weakening") == S.CLR_YELLOW
    assert S.rrg_color("Lagging") == S.CLR_RED
    assert S.rrg_color(None) == S.CLR_FLAT


def test_pcr_from_chain_sums_volume():
    chain = {
        "putExpDateMap": {"2026-06-20:6": {"500.0": [{"totalVolume": 30}]}},
        "callExpDateMap": {"2026-06-20:6": {"500.0": [{"totalVolume": 60}]}},
    }
    assert S.pcr_from_chain(chain) == 0.5       # 30 put / 60 call
    assert S.pcr_from_chain({}) is None
    assert S.pcr_from_chain({"callExpDateMap": {}}) is None  # cv == 0


def test_week_month_from_closes():
    closes = [float(i) for i in range(1, 31)]   # 1..30, last=30.0
    d3, wk, mo = S.week_month_from_closes(closes)
    # n=3 -> close[-4]=27 ; n=5 -> close[-6]=25 ; n=21 -> close[-22]=9
    assert round(d3, 4) == round((30 - 27) / 27 * 100, 4)
    assert round(wk, 4) == round((30 - 25) / 25 * 100, 4)
    assert round(mo, 4) == round((30 - 9) / 9 * 100, 4)


def test_week_month_short_series_returns_none():
    d3, wk, mo = S.week_month_from_closes([1.0, 2.0])
    assert d3 is None and wk is None and mo is None
```

**Step 2: Run — expect FAIL** (AttributeError). `..\.venv\Scripts\python -m pytest tests/test_sentiment_sectors.py -q`

**Step 3: Append to `webgui/pages/sentiment.py`** (after the existing pure transforms, before `_load_snapshots`). Add `CLR_FLAT` and `CLR_CYAN` near the other color constants if not present:

```python
CLR_FLAT = "#9e9e9e"
CLR_CYAN = "#3fb6c7"


def pct_color(pct):
    """Green up / red down / gray flat (|pct| < 0.05)."""
    if pct is None or abs(float(pct)) < 0.05:
        return CLR_FLAT
    return CLR_GREEN if float(pct) > 0 else CLR_RED


def pcr_color(pcr):
    """<0.95 call-dominated green, >1.05 put-dominated red, else flat."""
    if pcr is None or float(pcr) <= 0:
        return CLR_FLAT
    if float(pcr) < 0.95:
        return CLR_GREEN
    if float(pcr) > 1.05:
        return CLR_RED
    return CLR_FLAT


def rrg_color(quadrant):
    return {
        "Leading": CLR_GREEN, "Improving": CLR_CYAN,
        "Weakening": CLR_YELLOW, "Lagging": CLR_RED,
    }.get(quadrant, CLR_FLAT)


def pcr_from_chain(chain):
    """Sum put vs call totalVolume from a Schwab /chains payload -> ratio.
    Returns None when no chain or zero call volume. Ported from source
    sentiment_dashboard.py:2939-2953."""
    if not chain:
        return None
    pv = cv = 0
    for strikes in (chain.get("putExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    pv += v
    for strikes in (chain.get("callExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    cv += v
    return round(pv / cv, 3) if cv > 0 else None


def _pct_change_n(closes, n):
    """%-change from n sessions ago to last close, or None. Mirrors source
    _pct_change_n (uses close[-(n+1)])."""
    if not closes or len(closes) < n + 1:
        return None
    prev = float(closes[-(n + 1)])
    last = float(closes[-1])
    if prev == 0:
        return None
    return (last - prev) / prev * 100.0


def week_month_from_closes(closes):
    """(day3_pct, week_pct, month_pct) from a daily-close list (n=3/5/21)."""
    return (_pct_change_n(closes, 3),
            _pct_change_n(closes, 5),
            _pct_change_n(closes, 21))
```

**Step 4: Run — expect PASS** (6 passed). Then full suite to confirm no breakage: `..\.venv\Scripts\python -m pytest -q`.

**Step 5: Commit**
```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment_sectors.py
git commit -m "feat(sentiment): sector color/math pure helpers + P/C chain parser"
```

---

## Task 2: Sector table rows + summary + rotation banner transforms

**Files:**
- Modify: `webgui/pages/sentiment.py` (append more pure transforms)
- Test: `webgui/tests/test_sentiment_sectors.py` (append)

**Step 1: Append failing tests:**

```python
def _sector_data():
    # minimal 2-sector fixture mirroring sectors_ref row shape
    return [
        {"kind": "sector", "sector": "Information Technology", "label": "Information Technology",
         "etf": "XLK", "name": "Software, semis", "sp_weight": 32.53},
        {"kind": "sector", "sector": "Utilities", "label": "Utilities",
         "etf": "XLU", "name": "Electric, gas", "sp_weight": 2.09},
        {"kind": "industry", "sector": "Information Technology", "label": "Semis",
         "etf": "SMH", "name": "Semiconductors", "sp_weight": 0.0},
    ]


def test_sector_table_rows_built_and_sorted():
    quotes = {"XLK": {"change_pct": 1.0}, "XLU": {"change_pct": 2.0}}
    trends = {"XLK": {"week_pct": 3.0, "month_pct": 5.0},
              "XLU": {"week_pct": -1.0, "month_pct": 0.5}}
    pcr = {"XLK": 0.80, "XLU": 1.20}
    quads = {"XLK": "Leading", "XLU": "Lagging"}
    rows = S.sector_table_rows(_sector_data(), quotes, trends, pcr, quads)
    # only sector rows, sorted by day% desc -> XLU(2.0) before XLK(1.0)
    assert [r["etf"] for r in rows] == ["XLU", "XLK"]
    xlk = next(r for r in rows if r["etf"] == "XLK")
    assert xlk["sector"] == "Information Technology"
    assert xlk["day"] == 1.0 and xlk["week"] == 3.0 and xlk["month"] == 5.0
    assert xlk["pcr"] == 0.80 and xlk["rrg"] == "Leading"


def test_sector_summary_line():
    quotes = {"XLK": {"change_pct": 1.0}, "XLU": {"change_pct": -0.5}}
    line = S.sector_summary(_sector_data(), quotes)
    assert "% green" in line and "Cap-wtd" in line and "/10" in line


def test_rotation_banner_regimes():
    assert S.rotation_banner({"day_spread": 1.5})[0] == "STRONG RISK-ON"
    assert S.rotation_banner({"day_spread": 0.5})[0] == "RISK-ON"
    assert S.rotation_banner({"day_spread": -0.5})[0] == "RISK-OFF"
    assert S.rotation_banner({"day_spread": -1.5})[0] == "STRONG RISK-OFF"
    assert S.rotation_banner({"day_spread": 0.0})[0] == "MIXED"
    assert S.rotation_banner({})[0] == "—"


def test_rotation_banner_color_and_detail():
    regime, color, detail = S.rotation_banner({
        "day_spread": 0.5, "day_cyc": 0.7, "day_def": 0.2,
        "day_top3": ["Tech", "Financials", "Energy"],
        "day_bot3": ["Staples", "Utilities", "Health Care"]})
    assert color == S.CLR_GREEN
    assert detail.startswith("DAY:")
    assert "Tech" in detail and "Utilities" in detail
```

**Step 2: Run — expect FAIL.**

**Step 3: Append implementation** (uses copied engines):

```python
from scoring import sector_perf as scoring_sector  # noqa: E402  (top of file w/ others)
from scoring import rotation as scoring_rotation    # noqa: E402


def sector_table_rows(sector_data, quotes, trends, pcr, quadrants):
    """Build display rows for the 11 sectors, sorted by Day % desc (None last)."""
    rows = []
    for r in sector_data:
        if r.get("kind") != "sector":
            continue
        etf = r.get("etf")
        q = (quotes or {}).get(etf) or {}
        t = (trends or {}).get(etf) or {}
        rows.append({
            "sector": r.get("sector") or r.get("label"),
            "etf": etf,
            "desc": r.get("name") or "",
            "day": q.get("change_pct"),
            "week": t.get("week_pct"),
            "month": t.get("month_pct"),
            "pcr": (pcr or {}).get(etf),
            "rrg": (quadrants or {}).get(etf),
        })
    rows.sort(key=lambda r: (r["day"] is None, -(r["day"] or 0.0)))
    return rows


def sector_summary(sector_data, quotes):
    """'{pct_up}% green | Cap-wtd {wpct} | Score {score}/10' (mirrors source)."""
    pcts = []
    for r in sector_data:
        if r.get("kind") != "sector":
            continue
        q = (quotes or {}).get(r.get("etf")) or {}
        p = q.get("change_pct")
        if p is not None:
            pcts.append(p)
    if not pcts:
        return "No sector data returned"
    pct_up = sum(1 for p in pcts if p > 0) / len(pcts) * 100
    wpct, _ = scoring_sector.weighted_sector_pct(sector_data, quotes)
    wpct_str = f"{wpct:+.2f}%" if wpct is not None else "—"
    score = scoring_sector.sectors_score(sector_data, quotes)
    return f"{pct_up:.0f}% green | Cap-wtd {wpct_str} | Score {score:.1f}/10"


def rotation_banner(rot):
    """(regime, color, detail) from a compute_rotation() dict. Mirrors source
    _update_rotation_banner: day -> 3d -> week timeframe fallback."""
    if not rot:
        return "—", CLR_FLAT, "Refresh sector data to compute rotation"
    if rot.get("day_spread") is not None:
        tf, spread = "day", rot["day_spread"]
    elif rot.get("3d_spread") is not None:
        tf, spread = "3d", rot["3d_spread"]
    elif rot.get("week_spread") is not None:
        tf, spread = "week", rot["week_spread"]
    else:
        return "—", CLR_FLAT, "Refresh sector data to compute rotation"
    if spread >= 1.0:
        regime, color = "STRONG RISK-ON", CLR_GREEN
    elif spread >= 0.3:
        regime, color = "RISK-ON", CLR_GREEN
    elif spread <= -1.0:
        regime, color = "STRONG RISK-OFF", CLR_RED
    elif spread <= -0.3:
        regime, color = "RISK-OFF", CLR_RED
    else:
        regime, color = "MIXED", CLR_YELLOW
    cyc, dfn = rot.get(f"{tf}_cyc"), rot.get(f"{tf}_def")
    top = rot.get(f"{tf}_top3") or []
    bot = rot.get(f"{tf}_bot3") or []
    cyc_s = f"{cyc:+.2f}%" if cyc is not None else "—"
    def_s = f"{dfn:+.2f}%" if dfn is not None else "—"
    detail = (f"{tf.upper()}: Cyc {cyc_s} vs Def {def_s} (spread {spread:+.2f}%)"
              f"  ▲ {', '.join(top[:2]) or '—'}  ▼ {', '.join(bot[-2:]) or '—'}")
    return regime, color, detail
```

NOTE: the `from scoring import sector_perf ... rotation ...` imports go at the
TOP of the file with the existing `from scoring import ...` lines, not inline.

**Step 4: Run — expect PASS.** Confirm `compute_rotation`'s real output keys
(`day_spread`, `day_cyc`, `day_def`, `day_top3`, `day_bot3`, `3d_spread`,
`week_spread`, ...) by reading `sentiment-dashboard/scoring/rotation.py:38-132`
before finalizing — adjust key names in `rotation_banner` to the actual dict if
they differ from the assumptions above, and update the test fixture to match.
Then full suite.

**Step 5: Commit**
```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment_sectors.py
git commit -m "feat(sentiment): sector table rows + summary + rotation banner transforms"
```

---

## Task 3: Component table + tiles + rolling averages transforms

**Files:**
- Modify: `webgui/pages/sentiment.py`
- Test: `webgui/tests/test_sentiment_sectors.py` (append)

**Step 1: Append failing tests:**

```python
def _full_snap(total, **comp):
    base = {"vix_complex": 4, "put_call": 8, "breadth": 7,
            "rotation": 7, "sector_perf": 8, "credit_pulse": 6}
    base.update(comp)
    return {
        "date": "2026-06-12",
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 1.0 for k in base},
        "volatility": {"interpretation": "term backwardation"},
        "options": {"pc_equity": "0.860"},
        "breadth": {"interpretation": "Advancing"},
        "rotation": {"interpretation": "Day 7 · 3d 6 · Wk 7"},
    }


def test_component_table_rows_contrib():
    rows = S.component_table_rows(_full_snap(6.81), rotation_value=None,
                                  sector_value="+0.70%")
    by = {r["name"]: r for r in rows}
    # In-composite components only (no credit_pulse).
    assert "Credit Pulse" not in by
    vix = by["VIX Complex"]
    assert vix["score"] == 4 and vix["weight"] == "20%"
    assert abs(vix["contrib"] - 0.20 * 4 * 1.0) < 1e-9     # w*s*conf
    assert by["Sector Perf"]["value"] == "+0.70%"
    assert by["Put/Call"]["value"] == "0.860"


def test_tiles_from_score_band():
    t = S.tiles(_full_snap(6.81), prev_total=6.81)
    assert t["modifier"] == "1.10x" and t["signal"] == "Bullish"  # >=7? 6.81 -> >=5 band
    # 6.81 is in the >=5 band -> 1.00x / Neutral / Neutral
    assert t["modifier"] == "1.00x" and t["bias"] == "Neutral" and t["signal"] == "Neutral"
    assert t["yesterday"] == "6.81"
    assert t["change"] == "+0.00"


def test_tiles_strong_bands():
    assert S.tiles(_full_snap(9.2), None)["signal"] == "Strong Bull"
    assert S.tiles(_full_snap(2.0), None)["signal"] == "Strong Bear"


def test_rolling_averages_label():
    a5, a20, label = S.rolling_averages([5.0] * 4 + [6.0])  # recent higher
    assert label in ("Rising", "Falling", "Stable")
    rising = S.rolling_averages([4.0] * 19 + [9.0] * 6)
    assert rising[2] == "Rising"
```

(Fix the contradictory assert in `test_tiles_from_score_band` — keep ONLY the
6.81 → 1.00x/Neutral/Neutral assertions; delete the first `assert` line that
expected 1.10x. The plan author left both to make the band explicit; implement
to the >=5 band.)

**Step 2: Run — expect FAIL.**

**Step 3: Append implementation:**

```python
def _signal_band(score):
    """(size_modifier, bias, signal) — mirrors source _update_position_modifier."""
    if score >= 9:
        return "1.25x", "Long", "Strong Bull"
    if score >= 7:
        return "1.10x", "Long", "Bullish"
    if score >= 5:
        return "1.00x", "Neutral", "Neutral"
    if score >= 3:
        return "0.85x", "Cautious", "Bearish"
    return "0.70x", "Short", "Strong Bear"


def component_table_rows(snapshot, rotation_value=None, sector_value=None):
    """Rows for the in-composite components: name/value/score/weight/conf/contrib."""
    scores = snapshot.get("component_scores") or {}
    confs = snapshot.get("component_confidence") or {}
    value_src = {
        "vix_complex": (snapshot.get("volatility") or {}).get("interpretation"),
        "put_call": (snapshot.get("options") or {}).get("pc_equity"),
        "breadth": (snapshot.get("breadth") or {}).get("interpretation"),
        "rotation": rotation_value or (snapshot.get("rotation") or {}).get("interpretation"),
        "sector_perf": sector_value,
    }
    rows = []
    for key, name, w in COMPONENTS:
        if not w:                      # skip out-of-composite (credit_pulse)
            continue
        s = _safe_float(scores.get(key))
        c = _safe_float(confs.get(key))
        rows.append({
            "key": key, "name": name,
            "value": value_src.get(key) or "—",
            "score": int(s),
            "weight": f"{int(w * 100)}%",
            "conf": f"{int(c * 100)}%",
            "contrib": w * s * c,
        })
    return rows


def tiles(latest, prev_total):
    comp = latest.get("composite") or {}
    total = _safe_float(comp.get("total_score"))
    size, bias, signal = _signal_band(total)
    if prev_total is None:
        yest, change = "—", "—"
    else:
        yest = f"{_safe_float(prev_total):.2f}"
        change = f"{total - _safe_float(prev_total):+.2f}"
    return {"modifier": size, "bias": bias, "signal": signal,
            "yesterday": yest, "change": change}


def rolling_averages(prior_scores):
    """(a5, a20, label) — label Rising/Falling/Stable from 5d vs 20d means."""
    s = [x for x in prior_scores if x and x > 0]
    if not s:
        return 0.0, 0.0, "Stable"
    a5 = sum(s[-5:]) / len(s[-5:])
    a20 = sum(s[-20:]) / len(s[-20:])
    label = "Rising" if a5 > a20 + 0.3 else ("Falling" if a5 < a20 - 0.3 else "Stable")
    return round(a5, 2), round(a20, 2), label
```

**Step 4: Run — expect PASS.** Full suite.

**Step 5: Commit**
```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment_sectors.py
git commit -m "feat(sentiment): component-table/tiles/rolling-average transforms"
```

---

## Task 4: Data loader `_load_sector_perf` + render() wiring

**Files:**
- Modify: `webgui/pages/sentiment.py` (`_load_snapshots` area, `render()`, `_apply`)

**Step 1: Add `_load_sector_perf(spy_closes)`** near `_load_snapshots`:

```python
def _load_sector_perf(spy_closes):
    """Off-thread: fetch sector quotes + history + P/C + compute rotation/RRG.
    Returns a dict the page renders. spy_closes reused from the composite load."""
    import proxy
    import sectors_ref
    from datetime import date, timedelta

    sd = sectors_ref.load_sectors_data()
    etfs = [r["etf"] for r in sd if r.get("kind") == "sector" and r.get("etf")]

    try:
        quotes = proxy.schwab_client.get_quotes(etfs) or {}
    except Exception:
        quotes = {}

    trends, closes = {}, {}
    for etf in etfs:
        try:
            df = proxy.schwab_client.get_daily_history(etf, months=3)
        except Exception:
            df = None
        if df is None:
            continue
        cl = [float(c) for c in df["close"].tolist()]
        closes[etf] = cl
        d3, wk, mo = week_month_from_closes(cl)
        trends[etf] = {"day3_pct": d3, "week_pct": wk, "month_pct": mo}

    # P/C per sector ETF from /chains (mirrors source params).
    pcr = {}
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=30)).isoformat()
    for etf in etfs:
        try:
            chain = proxy.schwab_client._request("/chains", params={
                "symbol": etf, "contractType": "ALL", "range": "NTM",
                "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
        except Exception:
            chain = None
        v = pcr_from_chain(chain)
        if v is not None:
            pcr[etf] = v

    try:
        irx_q = proxy.schwab_client.get_quote("$IRX") or {}
        irx = irx_q.get("lastPrice") if isinstance(irx_q, dict) else None
    except Exception:
        irx = None

    quads = scoring_rotation.compute_rrg_quadrants(closes, spy_closes or [],
                                                   rs_window=50, mom_window=20)
    sp_weights = {r["etf"]: r.get("sp_weight", 0.0)
                  for r in sd if r.get("kind") == "sector" and r.get("etf")}
    try:
        dual = scoring_rotation.compute_dual_momentum(closes, sp_weights, irx, lookback_days=63)
    except Exception:
        dual = {}
    rot = scoring_rotation.compute_rotation(sd, trends, quotes)
    return {"sector_data": sd, "quotes": quotes, "trends": trends,
            "pcr": pcr, "quadrants": quads, "dual": dual, "rotation": rot}
```

Verify `get_quote("$IRX")` return shape against `proxy_client.py:236` and adjust
the `lastPrice` extraction if needed (it may return a nested dict).

**Step 2: Rework `render()`** — replace the component-cards block with the new
layout. Keep the existing gauge/date/bias/sub, history chart, velocity/divergence,
and trend-regime sections. Add:

- After `sub_lbl`: a **tiles row** — 5 `ui.card`/`ui.column` cells (MODIFIER, BIAS,
  SIGNAL, YESTERDAY, CHANGE) with title + value labels stored in a dict.
- Replace `_render_components` with a **component table** built via `ui.table`
  (columns: Component, Value, Score, Weight, Conf, Contrib) OR a `ui.grid`/rows;
  populate from `component_table_rows(latest, rotation_value, sector_value)`.
  Color the Score cell with `pct`-independent score color (reuse the gauge color
  or `CLR_GREEN/RED/YELLOW` by score band) — keep simple: green ≥7, red <4, else
  yellow.
- Under the history chart: a `roll_lbl` label set from `rolling_averages`.
- A new **Sector & Industry Performance** block: a section label, a "Refresh"
  `ui.button`, a `summary_lbl`, a `rotation_lbl` (banner), and a `ui.table` whose
  rows come from `sector_table_rows(...)`. Use a body-cell slot to color Day/Week/
  Month/P-C/RRG cells via `pct_color`/`pcr_color`/`rrg_color`. (If per-cell slot
  coloring is awkward, build the table with `ui.row`s per sector and color each
  `ui.label` via `.style(f"color:{...}")` — acceptable for 11 rows.)

Add to the page `state` dict: `"sector": None`. Add an async `load_sectors()`:

```python
    async def load_sectors():
        sec_spinner.visible = True
        try:
            state["sector"] = await ng_run.io_bound(_load_sector_perf, state["spy"])
            _apply_sectors()
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Sector load failed: {e}", type="negative")
        finally:
            sec_spinner.visible = False
```

In `_apply()` (composite), after computing everything, also derive `rotation_value`
from `state["sector"]["dual"].get("interp")` and `sector_value` from
`sector_summary`-style cap-wtd, and call the component-table builder + tiles +
rolling averages. In `load()`, after the composite loads, also `await load_sectors()`
(so SPY is available). Wire the Refresh button to run BOTH `load()` (which chains
sectors). Keep the 120 s timer calling `load()` — to bound /chains, make the timer
call a composite-only refresh; simplest acceptable approach: leave the 120 s timer
refreshing composite only and let sectors refresh on initial load + manual Refresh.

`_apply_sectors()` populates: `summary_lbl`, `rotation_lbl` (text+color via
`rotation_banner(state["sector"]["rotation"])`), and rebuilds the sector table
rows from `sector_table_rows(...)`. It also re-runs the component-table Value for
rotation/sector now that sector data exists.

**Step 3: Run full suite** (`..\.venv\Scripts\python -m pytest -q`) — transforms
green, `test_shell` green. Import smoke: `import main`.

**Step 4: Commit**
```bash
git add webgui/pages/sentiment.py
git commit -m "feat(sentiment): sector-perf loader + component table/tiles/sector section in render()"
```

---

## Task 5: Browser verify + docs

**Files:**
- Modify: root `CLAUDE.md`

**Step 1: Browser verify** — restart the `webgui` preview (stop+start; NiceGUI
doesn't hot-reload), navigate `/sentiment`, wait for both spinners. Screenshot.
Confirm: component table (6 cols, Contrib reconciles), 5 tiles, rolling-avg line,
and the Sector & Industry table with colored Day/Week/Month/P-C/RRG cells +
rotation banner + summary. Check `preview_logs` for the backfill/sector fetch and
`preview_console_logs` for errors. Weekend sparse data (missing /chains, "—") is
expected, not a bug.

**Step 2: Update root `CLAUDE.md`** — extend the `/sentiment` route description to
mention the sector-perf table + rotation banner + tiles; bump test count; note the
industry-expansion fast-follow.

**Step 3: Commit**
```bash
git add CLAUDE.md
git commit -m "docs: sentiment sector-perf section built"
```

---

## Gotchas

- Confirm `compute_rotation` output keys before finalizing `rotation_banner`
  (read `scoring/rotation.py:38-132`).
- `_request`/`get_quotes`/`get_quote` return shapes: verify in `proxy_client.py`.
- `ui.table` per-cell color: use a `body-cell-<col>` slot or fall back to manual
  `ui.row`s (11 rows — cheap).
- Component table Score/Conf/Contrib come from the SNAPSHOT (so Contrib reconciles
  to the gauge composite); rotation Value is the dual-momentum interp (documented
  divergence).
- Keep `render()` thin; page state in the local `state` dict.
- `options-scanner` has ~2 known unrelated date-relative test failures — ignore.
