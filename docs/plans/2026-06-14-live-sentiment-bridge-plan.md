# Live sentiment + bridge from GEX collector — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Compute a live intraday sentiment composite (reusing the copied scoring modules with current quotes), have the GEX collector write `shared/sentiment_bridge.json` every 5-min cycle, and switch the webgui Sentiment page to live (fall back to backfill off-hours).

**Architecture:** New shared `sentiment-dashboard/live_composite.py` (`compute_live`, `signal_band`, `build_bridge_payload`, `publish_bridge`) imported by both the collector and the page; a small defensively-imported hook in `gex_collector.run_collector_loop`; page rewire to prefer live during market hours.

**Tech Stack:** Python; reuses `scoring/` (`vix`, `put_call.score_sector_weighted`, `breadth`, `rotation.compute_dual_momentum`, `sector_perf`, `composite.blend/velocity/divergence`, `WEIGHTS`), `sectors_ref`, `bridge.write_bridge`; proxy `SchwabProxyClient`. pytest.

**Design:** [`2026-06-14-live-sentiment-bridge-design.md`](2026-06-14-live-sentiment-bridge-design.md)

**Tests:** sentiment-dashboard — `cd sentiment-dashboard && ..\.venv\Scripts\python -m pytest tests -q`; webgui — `cd webgui && ..\.venv\Scripts\python -m pytest -q`; options-scanner — `cd options-scanner && ..\.venv\Scripts\python -m pytest tests -q` (baseline has ~2 known date-relative failures — ignore). venv: `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`.

**Reference (read-only):** source live path in `D:\Trading With Schwab\sentiment-dashboard\sentiment_dashboard.py` (`calculate_all_scores` ~3526, `calculate_*_score`, `_fetch_worker`); the copied `history_backfill.py:_score_one_day` is the historical analog of the live path (same scoring calls).

---

## Task 1: `signal_band` + `build_bridge_payload` (pure) + tests

**Files:** Create `sentiment-dashboard/live_composite.py`; create `sentiment-dashboard/tests/test_live_composite.py`.

**Step 1 — failing tests** (`tests/test_live_composite.py`):
```python
"""Tests for live_composite pure helpers."""
import json
import live_composite as L


def _snap(total, **comp):
    base = {"vix_complex": 4, "put_call": 8, "breadth": 7, "rotation": 5, "sector_perf": 8}
    base.update(comp)
    return {
        "date": "2026-06-14",
        "composite": {"total_score": f"{total:.2f}"},
        "component_scores": base,
        "component_confidence": {k: 1.0 for k in base},
        "volatility": {"interpretation": "term flat"},
        "options": {"pc_equity": "0.86"},
        "breadth": {"interpretation": "Advancing"},
        "rotation": {"interpretation": "Cyc leads"},
    }


def test_signal_band():
    assert L.signal_band(9.5) == ("1.25x", "Long", "Strong Bull")
    assert L.signal_band(7.0) == ("1.10x", "Long", "Bullish")
    assert L.signal_band(5.0) == ("1.00x", "Neutral", "Neutral")
    assert L.signal_band(3.0) == ("0.85x", "Cautious", "Bearish")
    assert L.signal_band(1.0) == ("0.70x", "Short", "Strong Bear")


def test_build_bridge_payload_core():
    p = L.build_bridge_payload(_snap(6.81), history_scores=[6.0, 6.5, 6.8],
                               spy_closes=[], generated_at="2026-06-14T20:00:00+00:00")
    assert p["composite_score"] == 6.81
    assert p["regime"] == "bullish"          # >=6.5
    assert p["bias"] == "neutral"            # 6.81 -> band Neutral -> lowercased
    assert p["position_size_modifier"] == "1.00x"
    assert p["generated_at"] == "2026-06-14T20:00:00+00:00"
    assert p["date"] == "2026-06-14"
    assert set(["vix_complex", "put_call", "breadth", "rotation", "sector_perf"]) \
        <= set(p["component_scores"])
    assert "aggregate_confidence" in p and "velocity" in p and "weights" in p


def test_build_bridge_payload_regime_bands():
    band = lambda t: L.build_bridge_payload(_snap(t), [], [], "x")["regime"]
    assert band(8.5) == "strong_bullish"
    assert band(7.0) == "bullish"
    assert band(5.5) == "neutral"
    assert band(4.0) == "bearish"
    assert band(2.0) == "strong_bearish"


def test_build_bridge_payload_roundtrips_through_bridge(tmp_path):
    import bridge
    p = L.build_bridge_payload(_snap(6.0), [6.0], [], "2026-06-14T20:00:00+00:00")
    out = tmp_path / "b.json"
    bridge.write_bridge(p, path=out)
    reread = json.loads(out.read_text())
    assert reread["composite_score"] == 6.0
    assert reread["schema_version"]   # bridge adds it
```

**Step 2 — run, expect FAIL.**

**Step 3 — implement** `sentiment-dashboard/live_composite.py` (this task: imports + the two pure fns; `compute_live`/`publish_bridge` added in Tasks 2–3):
```python
"""Live intraday sentiment composite + bridge payload.

Shared by the GEX collector (headless 5-min publish) and the webgui Sentiment
page. Reuses the pure scoring modules with CURRENT quotes (the live analog of
history_backfill._score_one_day). No tk imports.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from scoring import WEIGHTS
from scoring import composite as scoring_composite

logger = logging.getLogger(__name__)

# Component display order + back-compat: which keys go in the bridge.
_BRIDGE_COMPONENTS = ("vix_complex", "put_call", "breadth", "rotation", "sector_perf")


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def signal_band(total):
    """(size_modifier, bias, signal) — mirrors source _update_position_modifier."""
    if total >= 9:
        return "1.25x", "Long", "Strong Bull"
    if total >= 7:
        return "1.10x", "Long", "Bullish"
    if total >= 5:
        return "1.00x", "Neutral", "Neutral"
    if total >= 3:
        return "0.85x", "Cautious", "Bearish"
    return "0.70x", "Short", "Strong Bear"


def build_bridge_payload(snapshot, history_scores, spy_closes, generated_at,
                         sector=None, trend=None):
    """Faithful bridge dict (mirrors source _build_bridge_payload), built from a
    live (or backfill) snapshot. `history_scores` = prior composite totals (for
    rolling avgs + velocity). `trend` = optional dict
    {state,label,description,raw_state,spy_close,sma_50,sma_200,sma_200_slope_pct,
    drawdown_pct,confidence}. `sector` = optional dict with quotes/dual for
    sector_breakdown + rotation_detail."""
    comp = snapshot.get("composite") or {}
    total = _safe_float(comp.get("total_score"))
    cs = snapshot.get("component_scores") or {}
    cc = snapshot.get("component_confidence") or {}
    scores = [s for s in (list(history_scores) + [total]) if s and s > 0]
    a5 = sum(scores[-5:]) / max(1, len(scores[-5:])) if scores else 0
    a20 = sum(scores[-20:]) / max(1, len(scores[-20:])) if scores else 0
    regime = ("strong_bullish" if total >= 8 else "bullish" if total >= 6.5
              else "neutral" if total >= 5 else "bearish" if total >= 3.5
              else "strong_bearish")
    momentum = ("rising" if a5 > a20 + 0.3 else "falling" if a5 < a20 - 0.3 else "stable")
    modifier, bias, signal = signal_band(total)
    vel = scoring_composite.velocity(list(history_scores), total)
    div = scoring_composite.divergence([
        (k, _safe_float(cs.get(k))) for k in _BRIDGE_COMPONENTS
        if _safe_float(cs.get(k)) > 0 and _safe_float(cc.get(k)) > 0])
    vix_c = _safe_float(cs.get("vix_complex"))
    sec_s = _safe_float(cs.get("sector_perf"))
    payload = {
        "source": "WebGUI-Sentiment",
        "generated_at": generated_at,
        "date": snapshot.get("date"),
        "composite_score": round(total, 2),
        "regime": regime,
        "bias": bias.lower(),
        "position_size_modifier": modifier,
        "contrarian_signal": signal,
        "momentum": momentum,
        "rolling_averages": {"5d": round(a5, 2), "20d": round(a20, 2)},
        "component_scores": {
            "vix_complex": vix_c, "vix": vix_c, "vix_term": vix_c,
            "vix1d": _safe_float(cs.get("vix1d")), "term_slope": _safe_float(cs.get("term_slope")),
            "put_call": _safe_float(cs.get("put_call")),
            "breadth": _safe_float(cs.get("breadth")),
            "flow": 0.0,
            "rotation": _safe_float(cs.get("rotation")),
            "sector": sec_s, "sector_perf": sec_s,
            "credit_pulse": _safe_float(cs.get("credit_pulse")),
        },
        "component_confidence": {k: round(_safe_float(v), 3) for k, v in cc.items()},
        "aggregate_confidence": round(
            sum(WEIGHTS[k] * _safe_float(cc.get(k)) for k in WEIGHTS), 3),
        "weights": dict(WEIGHTS),
        "velocity": {
            "roc_3d": (round(vel["roc_3d"], 2) if vel.get("roc_3d") is not None else None),
            "roc_5d": (round(vel["roc_5d"], 2) if vel.get("roc_5d") is not None else None),
            "z_20d": (round(vel["z_20d"], 2) if vel.get("z_20d") is not None else None),
            "regime_break": bool(vel.get("regime_break")),
        },
        "divergence_flag": div or None,
    }
    if trend:
        payload["trend_regime"] = {
            "state": trend.get("state"), "label": trend.get("label"),
            "description": trend.get("description"), "raw_state": trend.get("raw_state"),
            "spy_close": trend.get("spy_close"), "sma_50": trend.get("sma_50"),
            "sma_200": trend.get("sma_200"), "sma_200_slope_pct": trend.get("sma_200_slope_pct"),
            "drawdown_pct": trend.get("drawdown_pct"), "confidence": trend.get("confidence"),
        }
    if sector:
        bd = []
        for r in sector.get("sector_data", []):
            if r.get("kind") != "sector" or not r.get("etf"):
                continue
            pct = ((sector.get("quotes") or {}).get(r["etf"]) or {}).get("change_pct")
            if pct is not None:
                bd.append({"sector": r.get("sector"), "etf": r["etf"], "day_pct": round(float(pct), 4)})
        if bd:
            payload["sector_breakdown"] = bd
        dual = sector.get("dual") or {}
        if dual:
            payload["rotation_detail"] = {
                "method": "dual_momentum_v1",
                "crash_active": bool(dual.get("crash_active", False)),
                "cyc_avg_rank": dual.get("cyc_avg_rank"), "def_avg_rank": dual.get("def_avg_rank"),
                "rank_spread": dual.get("rank_spread"), "top_etf": dual.get("top_etf"),
                "ranks": dual.get("ranks", {}), "returns_63d": dual.get("returns", {}),
            }
    return payload
```

**Step 4 — run, expect PASS.** **Step 5 — commit:**
```bash
git add sentiment-dashboard/live_composite.py sentiment-dashboard/tests/test_live_composite.py
git commit -m "feat(sentiment): live_composite signal_band + build_bridge_payload (pure)"
```

---

## Task 2: `compute_live(schwab, sector_data, ...)`

**Files:** Modify `sentiment-dashboard/live_composite.py`; add a fake-client test to `tests/test_live_composite.py`.

Implement the live scoring path — the live analog of `history_backfill._score_one_day`, using CURRENT quotes. Model the per-component calls on `_score_one_day` (same scoring fns) but source inputs from quotes.

**Quote-shape note (VERIFY FIRST):** read `schwab-proxy/proxy_client.py` `SchwabProxyClient.get_quote`/`get_quotes` to confirm the price field. Earlier work found `get_quote(sym)` returns a dict with `"last"`. Confirm `get_quotes(list)` returns `{sym: {...}}` and which key holds the price/`change_pct`. Add a small `_last(qd)` helper that reads whichever field exists (`last`/`lastPrice`/`mark`). Do not assume.

```python
from scoring import vix as _vix
from scoring import put_call as _pc
from scoring import breadth as _breadth
from scoring import rotation as _rotation
from scoring import sector_perf as _sector
from scoring.types import ScoreResult

_VIX_SYMS = ["$VIX1D", "$VIX9D"]
_BREADTH = {"advn": ["$ADVN"], "decn": ["$DECN"],
            "nyhgh": ["$NYHGH", "$NYHGH.X", "$NEWH"],
            "nylow": ["$NYLOW", "$NYLOW.X", "$NEWL"],
            "pct50": ["$SPXA50R", "$NYA50R", "$MMFI"]}


def _last(qd):
    if not isinstance(qd, dict):
        return None
    for k in ("last", "lastPrice", "mark"):
        if qd.get(k) is not None:
            return _safe_float(qd.get(k))
    return None


def compute_live(schwab, sector_data, prior_vix1d=0.0, prior_sector_trends=None):
    """Compute the live intraday composite snapshot (backfill-snapshot shaped)."""
    sectors = [r["etf"] for r in sector_data if r.get("kind") == "sector" and r.get("etf")]
    sp_weights = {r["etf"]: r.get("sp_weight", 0.0)
                  for r in sector_data if r.get("kind") == "sector" and r.get("etf")}

    # --- quotes (batched) ---
    def _q(syms):
        try:
            return schwab.get_quotes(list(syms)) or {}
        except Exception:
            return {}
    vix_q = {}
    try:
        vix_q = {"$VIX": schwab.get_quote("$VIX") or {}}
    except Exception:
        pass
    vq = _q(_VIX_SYMS)
    bq = _q([s for v in _BREADTH.values() for s in v])
    sector_q = _q(sectors)
    try:
        irx = _last(schwab.get_quote("$IRX"))
    except Exception:
        irx = None

    vix = _last(vix_q.get("$VIX")) or 0.0
    # VIX 10d MA
    vix_ma = 0.0
    try:
        df = schwab.get_daily_history("$VIX", months=1)
        if df is not None:
            cl = [float(c) for c in df["close"].tolist()][-10:]
            vix_ma = sum(cl) / len(cl) if cl else 0.0
    except Exception:
        pass
    v1d = _last(vq.get("$VIX1D")) or 0.0
    v9d = _last(vq.get("$VIX9D")) or 0.0
    term = _vix.score_term(vix, vix_ma)
    v1d_r = _vix.score_vix1d(v1d, vix, _safe_float(prior_vix1d))
    slope = _vix.score_term_slope(v9d, vix)
    vix_complex = _vix.score_complex(term, v1d_r, slope)

    # --- breadth ---
    def _first(group):
        for s in _BREADTH[group]:
            v = _last(bq.get(s))
            if v is not None:
                return v
        return None
    advn, decn = _first("advn"), _first("decn")
    highs, lows = _first("nyhgh") or 0, _first("nylow") or 0
    pct50 = _first("pct50")
    ratio = (advn / decn) if (advn and decn) else (float("inf") if advn else None)
    net = ((advn or 0) - (decn or 0)) if (advn is not None or decn is not None) else None
    br = _breadth.score(pct_above_50=(f"{pct50:.1f}" if pct50 is not None else ""),
                        nyse_ad="Neutral", new_highs=float(highs), new_lows=float(lows),
                        breadth_ratio=ratio, breadth_numeric=net)

    # --- sector quotes -> last_quotes (day%) ---
    last_quotes = {}
    for etf in sectors:
        pct = (sector_q.get(etf) or {}).get("change_pct")
        if pct is not None:
            last_quotes[etf] = {"change_pct": pct}

    # --- per-sector P/C from /chains -> put_call ---
    from datetime import date, timedelta
    pcr = {}
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=30)).isoformat()
    for etf in sectors:
        try:
            chain = schwab._request("/chains", params={
                "symbol": etf, "contractType": "ALL", "range": "NTM",
                "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
        except Exception:
            chain = None
        v = _pcr_from_chain(chain)   # local copy of the parser (see below)
        if v is not None:
            pcr[etf] = v
    pc_res = _pc.score_sector_weighted(pcr, sp_weights)

    # --- sector close history -> dual momentum (rotation) + sector_perf ---
    closes = {}
    for etf in sectors:
        try:
            df = schwab.get_daily_history(etf, months=12)
        except Exception:
            df = None
        if df is not None:
            cl = [float(c) for c in df["close"].tolist()]
            if len(cl) >= 64:
                closes[etf] = cl
    dual = _rotation.compute_dual_momentum(
        closes, sp_weights, (irx / 10.0) if irx else None, lookback_days=63)
    rot_score = float(dual.get("score", 0) or 0)
    rot_conf = float(dual.get("confidence", 0.0) or 0.0)
    sec_score = _sector.sectors_score(sector_data, last_quotes)
    n_sec = sum(1 for e in sectors if e in last_quotes)
    sec_conf = (n_sec / 11.0) ** 0.5 if n_sec else 0.0

    scores = {"vix_complex": float(vix_complex.score), "put_call": float(pc_res.score),
              "breadth": float(br.score), "rotation": rot_score, "sector_perf": float(sec_score)}
    confs = {"vix_complex": float(vix_complex.confidence), "put_call": float(pc_res.confidence),
             "breadth": float(br.confidence), "rotation": rot_conf, "sector_perf": sec_conf}
    composite, agg = scoring_composite.blend(scores, confs, WEIGHTS)
    modifier, bias, _sig = signal_band(composite)
    return {
        "date": date.today().isoformat(),
        "source": "live",
        "composite": {"total_score": f"{composite:.2f}", "bias": bias,
                      "size_modifier": modifier, "aggregate_confidence": round(agg, 3)},
        "component_scores": {**scores, "credit_pulse": 0.0},
        "component_confidence": {**{k: round(v, 3) for k, v in confs.items()}, "credit_pulse": 0.0},
        "volatility": {"interpretation": vix_complex.interp},
        "options": {"pc_equity": "", "interpretation": pc_res.interp},
        "breadth": {"interpretation": br.interp},
        "rotation": {"interpretation": dual.get("interp", "")},
        "_sector_runtime": {"sector_data": sector_data, "quotes": last_quotes, "dual": dual, "closes": closes},
        "_vix1d": v1d,
    }
```
Add a module-level `_pcr_from_chain(chain)` copied from `webgui/pages/sentiment.py:pcr_from_chain` (sum put/call totalVolume → ratio; None when no chain/zero calls). (It lives in the webgui page; copy the ~15-line body here so sentiment-dashboard has no webgui dependency.)

**Fake-client test** (orchestration, no network):
```python
class _FakeDF:
    def __init__(self, closes): self._c = closes
    def __getitem__(self, k): return self
    def tolist(self): return self._c

class _FakeClient:
    def get_quote(self, s): return {"last": 18.0} if s == "$VIX" else {"last": 1.5}
    def get_quotes(self, syms): return {s: {"last": 17.0, "change_pct": 0.5} for s in syms}
    def get_daily_history(self, s, months=12):
        return _FakeDF([100.0 + i for i in range(80)])
    def _request(self, ep, params=None): return None  # no chains -> put_call conf 0

def test_compute_live_smoke():
    import sectors_ref
    sd = sectors_ref.load_sectors_data()
    snap = L.compute_live(_FakeClient(), sd)
    assert 0 <= float(snap["composite"]["total_score"]) <= 10
    assert set(["vix_complex","put_call","breadth","rotation","sector_perf"]) \
        <= set(snap["component_scores"])
```
(`_FakeDF.__getitem__` returning self lets `df["close"].tolist()` work.)

**Run** sentiment-dashboard tests (expect green). **Commit:** `feat(sentiment): compute_live live intraday scoring path`.

---

## Task 3: `publish_bridge` + GEX collector hook

**Files:** Modify `sentiment-dashboard/live_composite.py` (add `publish_bridge`); modify `options-scanner/gex_collector.py` (hook + helper); add a collector test to `options-scanner/tests/test_gex_collector.py`.

**3a. `publish_bridge(schwab=None)` in live_composite.py:**
```python
def _resolve_proxy_client():
    import sys, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from repo_paths import SCHWAB_PROXY, PROXY_URL
    if str(SCHWAB_PROXY) not in sys.path:
        sys.path.insert(0, str(SCHWAB_PROXY))
    from proxy_client import SchwabProxyClient
    return SchwabProxyClient(PROXY_URL)


def publish_bridge(schwab=None):
    """Compute the live composite and write shared/sentiment_bridge.json.
    Self-contained + defensive; returns the payload or None on failure."""
    try:
        import sectors_ref
        from scoring import trend_regime as _tr
        import bridge
        if schwab is None:
            schwab = _resolve_proxy_client()
        sd = sectors_ref.load_sectors_data()
        snap = compute_live(schwab, sd)
        sector_rt = snap.pop("_sector_runtime", None)
        # trend regime from SPY 12mo
        spy_closes = []
        try:
            df = schwab.get_daily_history("SPY", months=12)
            spy_closes = [float(c) for c in df["close"].tolist()] if df is not None else []
        except Exception:
            pass
        trend = None
        if spy_closes:
            r = _tr.classify(spy_closes)
            trend = {"state": r.state, "label": r.label, "description": r.description,
                     "raw_state": r.state, "spy_close": round(r.spy_close, 4),
                     "sma_50": round(r.sma_50, 4), "sma_200": round(r.sma_200, 4),
                     "sma_200_slope_pct": round(r.sma_200_slope_pct, 4),
                     "drawdown_pct": round(r.drawdown_pct, 4), "confidence": round(r.confidence, 3)}
        gen = datetime.now(timezone.utc).isoformat()
        payload = build_bridge_payload(snap, history_scores=[], spy_closes=spy_closes,
                                       generated_at=gen, sector=sector_rt, trend=trend)
        bridge.write_bridge(payload)
        logger.info("sentiment bridge published: score=%s regime=%s",
                    payload.get("composite_score"), payload.get("regime"))
        return payload
    except Exception:
        logger.exception("publish_bridge failed")
        return None
```

**3a-bis. Standalone entry `sentiment-dashboard/publish_bridge.py`** (so the collector can run it in a CLEAN process — see the collision gotcha):
```python
#!/usr/bin/env python
"""Standalone: compute the live composite and write shared/sentiment_bridge.json.
Run by the GEX collector each cycle (in a subprocess) and usable manually.
Rooted in sentiment-dashboard so `import scoring` resolves to THIS package, not
the options-scanner scoring.py (the cross-app collision the root CLAUDE.md warns of)."""
import logging, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # sentiment scoring/ package wins
sys.path.insert(0, str(HERE.parent))     # repo root for repo_paths
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def main() -> int:
    import live_composite
    payload = live_composite.publish_bridge()
    return 0 if payload else 1

if __name__ == "__main__":
    sys.exit(main())
```

**3b. GEX collector hook** — in `options-scanner/gex_collector.py`. **Run the publish in a SUBPROCESS** (clean `sys.modules`/`sys.path` → no `scoring` collision with options-scanner's own `scoring.py`; also isolates any hang/crash from the collector):
```python
def _publish_sentiment_bridge():
    """Best-effort: publish the sentiment bridge in a clean subprocess. Never raises."""
    try:
        import subprocess, sys, pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from repo_paths import SENTIMENT
        script = SENTIMENT / "publish_bridge.py"
        subprocess.run([sys.executable, str(script)], timeout=150,
                       capture_output=True)
    except Exception:
        log.exception("sentiment bridge publish failed; continuing")
```
In `run_collector_loop`, after the GEX `poll(...)` try/except (the block at ~334-339), add:
```python
        try:
            _publish_sentiment_bridge()
        except Exception:
            log.exception("sentiment hook crashed; continuing")
```
(The helper already swallows; the outer guard is belt-and-suspenders so the loop is never broken.)

**3c. Collector test** (`tests/test_gex_collector.py`):
```python
def test_sentiment_hook_failure_does_not_break_poll(monkeypatch):
    import gex_collector as gc
    calls = {"poll": 0}
    def good_poll(c, e, cn): calls["poll"] += 1
    monkeypatch.setattr(gc, "_publish_sentiment_bridge",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # one boundary then stop
    import threading
    ev = threading.Event()
    clock = ... # use the test's existing clock helper / or a tiny fake within START..STOP
    # Simplest: call the hook directly and assert it doesn't raise out of the guarded path.
    # If the file's existing loop tests have a harness, reuse it; otherwise assert:
    try:
        gc._publish_sentiment_bridge()  # real helper swallows internally
    except Exception:
        raise AssertionError("hook must never raise")
```
Keep the test minimal and consistent with the file's existing style (reuse its loop/clock harness if present; otherwise assert `_publish_sentiment_bridge` never raises). Do NOT perturb the existing collector tests.

**Run** options-scanner tests (ignore the ~2 known date-relative failures; no NEW failures). **Run** sentiment-dashboard tests. **Commit:** `feat(sentiment): publish bridge from GEX collector cycle (+ live publish_bridge)`.

---

## Task 4: webgui page → live intraday (with backfill fallback) + page-open publish

**Files:** Modify `webgui/pages/sentiment.py`.

- Add a market-hours check + a `_load_composite()` that, off-thread, tries
  `live_composite.compute_live(proxy.schwab_client, sector_data)` during RTH and
  returns `(live_snapshot_as_1elem_list_or_history, spy)`; on failure or off-hours
  falls back to the existing `_load_snapshots` (backfill). Simplest wiring: keep
  `_load_snapshots` (backfill, used for the 30-day history series regardless), and
  ADD the live snapshot as the "latest" used by `_apply` for gauge/components/tiles.
  Concretely: `state["snaps"]` keeps the backfill history; add `state["live"]` =
  the live snapshot (or None). `_apply` uses `latest = state["live"] or state["snaps"][-1]`.
  velocity uses the backfill history series + the live total.
- Replace the page's local `_signal_band` with `from live_composite import signal_band`
  (import at top via the SENTIMENT path, alongside the scoring imports — eager, no
  lazy scoring import). Keep `tiles` using it.
- `_publish_bridge()` page helper: build via `live_composite.build_bridge_payload(
  latest_snapshot, history_scores, spy, generated_at=now, sector=state["sector"]
  enriched, trend=...)` and `bridge.write_bridge(...)`, in try/except. Call at end
  of `_apply()` and `_apply_sectors()`.
- Market-hours helper: a small pure `is_rth(now)` (Mon–Fri 08:30–15:00 CT) — unit-test it.
- Off-thread + spinner + cache (`_CACHE["live"]`) consistent with existing pattern.

**Tests:** unit-test `is_rth`. Verify full webgui suite green + `import main`.
**Commit:** `feat(sentiment): page uses live intraday composite (RTH) + publishes bridge`.

---

## Task 5: Verify + docs

1. **Script-verify** (temp `webgui/_verify_live.py`, deleted after): build sector_data, call `live_composite.compute_live(proxy.schwab_client, sd)` → print composite + components; call `live_composite.publish_bridge()` → read back `shared/sentiment_bridge.json`; then `from regime_filter import evaluate_regime; print(evaluate_regime())` to confirm the consumer reads it without error. Run with `$env:PYTHONIOENCODING="utf-8"`.
2. **Browser** (best-effort given slow proxy): restart preview, `/sentiment`, confirm gauge/components reflect live during RTH (or backfill fallback off-hours) + bridge written (status bar / proxy).
3. **Docs:** root `CLAUDE.md` — note the live composite + collector-published bridge + page live/RTH fallback; the sentiment-dashboard `CLAUDE.md` — add `live_composite.py` to the file table + "the GEX collector publishes the bridge each cycle"; bump webgui test count. Note the source `bridge.py` `BRIDGE_SCHEMA_VERSION`/`source` field.
4. **Commit** docs.

---

## Gotchas
- **`scoring` cross-app collision (load-bearing).** options-scanner has a top-level `scoring.py`; sentiment-dashboard has a `scoring/` package. The collector process runs with options-scanner on `sys.path[0]`, so any in-process `from scoring import composite` (which `live_composite` does at import) would resolve to the WRONG module. **Therefore the collector publishes via a SUBPROCESS** (`publish_bridge.py`, cwd/sys.path[0] = sentiment-dashboard) — a clean interpreter where `import scoring` resolves to sentiment's package. Do NOT import `live_composite` in the collector process. (The webgui page CAN import `live_composite` directly: in the webgui process sentiment's `scoring/` is the bound one — `sentiment.py` already imports it eagerly at top; add the `live_composite` import to that same eager top block, never lazily inside a function.)
- Verify proxy quote field (`last` vs `lastPrice`) before trusting `_last`.
- `compute_live` is ~15–18 proxy calls; fine per 5-min cycle and per page load.
- Collector client ≠ proxy client → `publish_bridge` self-resolves a `SchwabProxyClient`.
- options-scanner has ~2 known date-relative test failures — ignore; just don't add new ones.
- `bridge.write_bridge` dual-writes canonical + legacy; default (no path) is correct for production.
