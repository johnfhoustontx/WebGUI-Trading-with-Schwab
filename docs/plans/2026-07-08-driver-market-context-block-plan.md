# Driver market-context block — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (this
> session) or superpowers:executing-plans (separate session) to implement this plan
> task-by-task.

**Goal:** Feed the autonomous Driver's Claude decider a compact `market_read` (per-index
gamma flip/walls/what-if from the freshest gamma_analyze briefing + live spot, market
breadth/risk-on-off, sentiment 0-10 score) as **reasoning context only** — no new hard
gates.

**Architecture:** The Driver stays a Tier-1/2 consumer (Redis caches + one proxy quote
call, no engine/DB imports). The handler reads the caches into an enriched `market`
dict; a pure `compute._market_read(market)` assembles a structured `market_read`
appended to the decision packet (mirrors the existing additive `market_state` line); the
decider gets a short guidance paragraph; a one-line summary is surfaced on the `/driver`
decision log. `guardrails.py` is **untouched** (the wall-aware gate is a deferred
follow-up). See the design doc: `docs/plans/2026-07-08-driver-market-context-block-design.md`.

**Tech Stack:** Python 3.11, pytest, `shared.bus` (fakeredis under pytest), the driver_svc
process-isolated engine layer.

**Conventions for every task:**
- Run tests per folder from the repo root: `.venv\Scripts\python -m pytest services\driver_svc -q`
  (NEVER `pytest services` over all services — it re-triggers the `config` collision).
- No live Claude or proxy in tests (monkeypatch `compute.requests.get` /
  `services.driver_svc.decider.decide`, seed `fake_bus`).
- Every new function is **defensive** (degrades, never raises) — house style.
- Commit after each task; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- PAPER ONLY — never touch `config.PAPER_TRADE`. `guardrails.py` stays unchanged.

---

### Task 1: `fetch_market_context` gains live SPY/QQQ spot

**Files:**
- Modify: `services/driver_svc/compute.py:274` (`fetch_market_context`)
- Test: `services/driver_svc/tests/test_compute_packet.py` (the `fetch_market_context` block, ~line 308)

**Step 1: Write the failing test** — append to `test_compute_packet.py`:

```python
def test_fetch_market_context_includes_etf_spots(monkeypatch):
    """SPY/QQQ are ETFs (flat quote shape) — their spot rides along for the market read."""
    payload = {
        "$VIX": {"quote": {"lastPrice": 13.5}},
        "$SPX": {"quote": {"lastPrice": 5500.0}},
        "$VIX1D": {"quote": {"lastPrice": 12.0}},
        "SPY": {"assetMainType": "EQUITY", "lastPrice": 598.2},   # flat (ETF) shape
        "QQQ": {"assetMainType": "EQUITY", "lastPrice": 521.4},
    }
    monkeypatch.setattr(compute.requests, "get", lambda *a, **k: _Resp(payload))
    ctx = compute.fetch_market_context()
    assert ctx["spy_spot"] == 598.2 and ctx["qqq_spot"] == 521.4
    assert ctx["vix"] == 13.5 and ctx["spx_spot"] == 5500.0    # unchanged


def test_fetch_market_context_missing_etf_spot_is_none(monkeypatch):
    monkeypatch.setattr(compute.requests, "get",
                        lambda *a, **k: _Resp({"$VIX": {"quote": {"lastPrice": 20.0}}}))
    ctx = compute.fetch_market_context()
    assert ctx["spy_spot"] is None and ctx["qqq_spot"] is None
```

**Step 2: Run to verify it fails** — `KeyError: 'spy_spot'`.
`.venv\Scripts\python -m pytest services\driver_svc\tests\test_compute_packet.py -k fetch_market_context -q`

**Step 3: Implement** — in `fetch_market_context`, extend the symbols + return dict:

```python
        resp = requests.get(f"{PROXY_URL}/quotes",
                            params={"symbols": "$VIX,$SPX,$VIX1D,SPY,QQQ"}, timeout=10)
        resp.raise_for_status()
        quotes = resp.json() or {}
        return {
            "vix": _index_price(quotes.get("$VIX", {})),
            "spx_spot": _index_price(quotes.get("$SPX", {})),
            "vix1d": _index_price(quotes.get("$VIX1D", {})),
            "spy_spot": _index_price(quotes.get("SPY", {})),
            "qqq_spot": _index_price(quotes.get("QQQ", {})),
        }
```

(`_index_price` already checks both the nested-index and flat-ETF shapes, so SPY/QQQ parse.)

**Step 4: Run to verify pass.** Also run the whole `fetch_market_context` block + the
existing `test_fetch_market_context_*` (they still pass — additive).

**Step 5: Commit** — `feat(driver): fetch_market_context includes live SPY/QQQ spot`

---

### Task 2: pure `_dashboard_risk_read` (breadth + risk-on/off)

**Files:**
- Modify: `services/driver_svc/compute.py` (add helper near `_market_state_line`)
- Test: `services/driver_svc/tests/test_compute_packet.py`

**Step 1: Write the failing test:**

```python
def test_dashboard_risk_read_breadth_and_risk():
    dash = {"categories": [
        {"category": "Breadth", "tiles": [
            {"display": "$ADVN-$DECN", "last": -465.0, "color_state": "risk_off_mild"},
            {"display": "VIX", "color_state": "risk_off_strong"}]},
        {"category": "Index", "tiles": [
            {"display": "SPX", "color_state": "risk_off_mild"}]}]}
    out = compute._dashboard_risk_read(dash)
    assert out["breadth_spread"] == -465.0
    assert out["risk"] == "risk_off"        # net color_state tilt is negative


def test_dashboard_risk_read_risk_on_and_missing_breadth():
    dash = {"categories": [{"category": "X", "tiles": [
        {"display": "SPY", "color_state": "risk_on_strong"},
        {"display": "XLK", "color_state": "risk_on_mild"}]}]}
    out = compute._dashboard_risk_read(dash)
    assert out["risk"] == "risk_on" and "breadth_spread" not in out


def test_dashboard_risk_read_empty_is_empty():
    for d in ({}, None, {"categories": []}, {"categories": [{"tiles": []}]}, "junk"):
        assert compute._dashboard_risk_read(d) == {}
```

**Step 2: Run to verify it fails** (`AttributeError: module has no attribute '_dashboard_risk_read'`).

**Step 3: Implement** — add to `compute.py`:

```python
# Market-dashboard tile color_state → a signed risk tilt (risk-on positive).
_RISK_WEIGHT = {"risk_on_strong": 2, "risk_on_mild": 1, "flat": 0,
                "risk_off_mild": -1, "risk_off_strong": -2, "no_data": 0}


def _dashboard_risk_read(dashboard) -> dict:
    """Breadth spread + an aggregate risk-on/off label from ``cache:market:dashboard``.

    Reads the ``$ADVN-$DECN`` tile's ``last`` (the breadth spread) and sums every tile's
    ``color_state`` into a net tilt → ``risk_on`` / ``neutral`` / ``risk_off``. Defensive
    → ``{}`` on a missing / empty / malformed dashboard. Never raises.
    """
    try:
        cats = (dashboard or {}).get("categories") or []
        tiles = [t for c in cats for t in (c.get("tiles") or []) if isinstance(t, dict)]
        if not tiles:
            return {}
        breadth = next((t.get("last") for t in tiles
                        if t.get("display") == "$ADVN-$DECN"), None)
        score = sum(_RISK_WEIGHT.get(t.get("color_state"), 0) for t in tiles)
        out = {"risk": "risk_on" if score > 0 else "risk_off" if score < 0 else "neutral"}
        if breadth is not None:
            out["breadth_spread"] = breadth
        return out
    except Exception:  # noqa: BLE001 — context is best-effort.
        return {}
```

**Step 4: Run to verify pass.**

**Step 5: Commit** — `feat(driver): pure _dashboard_risk_read (breadth + risk-on/off)`

---

### Task 3: pure `_pick_latest_briefing` (freshest TODAY gamma briefing)

**Files:**
- Modify: `services/driver_svc/compute.py`
- Test: `services/driver_svc/tests/test_compute_packet.py`

**Step 1: Write the failing test:**

```python
import datetime as _dt

def _brief(slot, gen, bias=-20):
    return {"slot": slot, "generated_at": gen,
            "analysis": {"bias": bias, "regime": "neg gamma",
                         "indices": [{"symbol": "$SPX", "gamma_flip": 6005}]}}

def test_pick_latest_briefing_newest_today():
    today = _dt.date(2026, 7, 8)
    payloads = [_brief("open", "2026-07-08T08:48:00-05:00", bias=-10),
                _brief("midday", "2026-07-08T11:30:00-05:00", bias=-35)]
    out = compute._pick_latest_briefing(payloads, today)
    assert out["bias"] == -35 and out["_slot"] == "midday"      # newest wins
    assert out["_generated_at"].startswith("2026-07-08T11:30")

def test_pick_latest_briefing_drops_prior_day():
    today = _dt.date(2026, 7, 8)
    out = compute._pick_latest_briefing(
        [_brief("close", "2026-07-07T14:58:00-05:00")], today)   # yesterday only
    assert out is None                                           # stale gamma dropped

def test_pick_latest_briefing_skips_no_analysis_and_junk():
    today = _dt.date(2026, 7, 8)
    payloads = [None, "junk", {"slot": "open", "generated_at": "2026-07-08T08:48:00-05:00",
                               "analysis": None},               # degraded page → skip
                _brief("midday", "2026-07-08T11:30:00-05:00")]
    out = compute._pick_latest_briefing(payloads, today)
    assert out and out["_slot"] == "midday"

def test_pick_latest_briefing_empty_is_none():
    assert compute._pick_latest_briefing([], _dt.date(2026, 7, 8)) is None
```

**Step 2: Run to verify it fails.**

**Step 3: Implement** — add to `compute.py`:

```python
def _pick_latest_briefing(payloads, today_ct):
    """The freshest TODAY gamma_analyze ``analysis`` across the scheduled-slot payloads.

    ``payloads`` = the scheduled-slot payloads (``{analysis, slot, generated_at}``; keys
    tolerated absent). Keeps only those whose ``generated_at`` date == ``today_ct`` (a
    prior-session briefing's walls mislead → dropped) with a non-empty ``analysis``, and
    returns the latest by ``generated_at`` (ISO sorts lexically), stamping
    ``_slot``/``_generated_at`` onto a COPY. ``None`` when nothing usable. Never raises.
    """
    try:
        today = today_ct.isoformat() if hasattr(today_ct, "isoformat") else str(today_ct)
        best = None
        for p in payloads or []:
            if not isinstance(p, dict):
                continue
            analysis, gen = p.get("analysis"), str(p.get("generated_at") or "")
            if not isinstance(analysis, dict) or not analysis or gen[:10] != today:
                continue
            if best is None or gen > best[0]:
                best = (gen, p.get("slot"), analysis)
        if best is None:
            return None
        gen, slot, analysis = best
        return {**analysis, "_slot": slot, "_generated_at": gen}
    except Exception:  # noqa: BLE001 — context is best-effort.
        return None
```

**Step 4: Run to verify pass.**

**Step 5: Commit** — `feat(driver): pure _pick_latest_briefing (freshest today gamma briefing)`

---

### Task 4: `_market_read` + `build_packet` wiring

**Files:**
- Modify: `services/driver_svc/compute.py` (add `_posture`, `_as_of`, `_market_read_summary`,
  `_market_read`; wire into `build_packet` after the `market_state` block ~line 206)
- Test: `services/driver_svc/tests/test_compute_packet.py`

**Step 1: Write the failing test:**

```python
def _market_ctx():
    return {
        "vix": 15.0, "spx_spot": 5980.0, "spy_spot": 598.0, "qqq_spot": 521.0,
        "briefing": {"_slot": "midday", "_generated_at": "2026-07-08T12:30:00-05:00",
            "regime": "negative gamma below flip", "bias": -35, "bias_label": "bearish",
            "headline": "Dealers short gamma.", "indices": [
                {"symbol": "$SPX", "spot": 5975, "gamma_flip": 6005, "put_wall": 5900,
                 "call_wall": 6050, "max_pain": 5975, "expected_move": 46, "pc_ratio": 1.3,
                 "what_if": {"rally": "r", "selloff": "s", "chop": "c"}},
                {"symbol": "SPY", "gamma_flip": 600, "put_wall": 590, "call_wall": 605},
                {"symbol": "QQQ", "gamma_flip": 523, "put_wall": 515, "call_wall": 528}]},
        "dashboard": {"categories": [{"category": "B", "tiles": [
            {"display": "$ADVN-$DECN", "last": -620.0, "color_state": "risk_off_mild"},
            {"display": "VIX", "color_state": "risk_off_strong"}]}]},
        "sentiment": {"score": 4.1, "bias": "bearish"}}

def test_market_read_full_assembly():
    mr = compute._market_read(_market_ctx())
    assert mr["regime"] == "negative gamma below flip" and mr["bias"] == -35
    assert mr["breadth_spread"] == -620.0 and mr["risk"] == "risk_off"
    assert mr["sentiment_score"] == 4.1 and mr["sentiment_bias"] == "bearish"
    spx = next(i for i in mr["indices"] if i["symbol"] == "$SPX")
    assert spx["spot"] == 5980.0                       # LIVE spot overrides briefing 5975
    assert spx["flip"] == 6005 and spx["put_wall"] == 5900
    assert spx["posture"] == "below flip (negative gamma)"
    assert "midday" in mr["as_of"] and "12:30" in mr["as_of"]
    assert mr["summary"]                               # one-line summary present

def test_market_read_degrades_partial():
    # No briefing → no gamma lines, but breadth + sentiment still present.
    mr = compute._market_read({"dashboard": _market_ctx()["dashboard"],
                               "sentiment": {"score": 6.0, "bias": "bullish"}})
    assert "indices" not in mr and "regime" not in mr
    assert mr["breadth_spread"] == -620.0 and mr["sentiment_score"] == 6.0

def test_market_read_all_absent_is_empty():
    for m in ({}, None, {"vix": 15.0}, {"briefing": None, "dashboard": None}):
        assert compute._market_read(m) == {}

# build_packet wiring
def test_build_packet_includes_market_read():
    import json
    pkt = compute.build_packet({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                               market=_market_ctx())
    assert "market_read" in pkt
    blob = json.dumps(pkt, default=str)
    assert "put_wall" in blob and "negative gamma below flip" in blob

def test_build_packet_market_read_absent_backcompat():
    """No market-read sources → NO market_read key (byte-identical to today)."""
    pkt = compute.build_packet({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                               market={"vix": 14.0})
    assert "market_read" not in pkt

def test_market_read_is_context_only_not_a_filter():
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 200.0,
                              "composite_score": 60, "expiration": "2026-06-24"}],
            "signals_swing": []}
    base = compute.build_packet(scan, {"snapshot": {}}, target=500.0, limits=_lim(), market={})
    withmr = compute.build_packet(scan, {"snapshot": {}}, target=500.0, limits=_lim(),
                                  market=_market_ctx())
    assert withmr["menu"] == base["menu"] and withmr["menu_by_id"] == base["menu_by_id"]
```

**Step 2: Run to verify it fails.**

**Step 3: Implement** — add helpers + wire `build_packet`:

```python
_READ_INDEX_SYMBOLS = ("$SPX", "SPY", "QQQ")
_SPOT_KEY = {"$SPX": "spx_spot", "SPY": "spy_spot", "QQQ": "qqq_spot"}


def _posture(spot, flip) -> str:
    """One-word gamma posture from spot vs the gamma flip ('' if unknown)."""
    try:
        if spot is None or flip is None:
            return ""
        return ("below flip (negative gamma)" if float(spot) < float(flip)
                else "above flip (positive gamma)")
    except (TypeError, ValueError):
        return ""


def _as_of(briefing) -> str:
    """A short 'slot HH:MM CT' freshness stamp from the briefing meta ('' if unknown)."""
    slot = str(briefing.get("_slot") or "").strip()
    gen = str(briefing.get("_generated_at") or "")
    hhmm = gen[11:16] if len(gen) >= 16 else ""
    return " ".join(x for x in (slot, (hhmm + " CT") if hhmm else "") if x).strip()


def _market_read_summary(read) -> str:
    """One-line summary for the /driver decision log (regime · bias · breadth · sent)."""
    parts = []
    if read.get("regime"):
        parts.append(str(read["regime"]))
    if read.get("bias") is not None:
        parts.append(f"bias {read['bias']}")
    if read.get("breadth_spread") is not None:
        parts.append(f"breadth {read['breadth_spread']} {read.get('risk', '')}".strip())
    elif read.get("risk"):
        parts.append(str(read["risk"]))
    if read.get("sentiment_score") is not None:
        parts.append(f"sent {read['sentiment_score']}")
    return " · ".join(parts)


def _market_read(market) -> dict:
    """Assemble the decider's ``market_read`` from the enriched market context (pure).

    Joins the freshest gamma briefing (``market['briefing']`` — regime/bias/headline +
    per-index flip/walls/what-if), a LIVE per-index spot
    (``market['{spx,spy,qqq}_spot']`` from ``fetch_market_context``; briefing spot is
    the fallback), the market-dashboard breadth/risk (``market['dashboard']``), and the
    sentiment 0-10 score/bias (``market['sentiment']``). ``{}`` when NONE of the three is
    usable (→ ``build_packet`` omits the key; byte-identical to today). Never raises — a
    partial context yields a partial read. REASONING CONTEXT ONLY (no hard rule).
    """
    try:
        m = market or {}
        briefing = m.get("briefing") if isinstance(m.get("briefing"), dict) else None
        dash = _dashboard_risk_read(m.get("dashboard"))
        sent = m.get("sentiment") if isinstance(m.get("sentiment"), dict) else None
        read = {}
        if briefing:
            as_of = _as_of(briefing)
            if as_of:
                read["as_of"] = as_of
            for k in ("regime", "bias", "bias_label", "headline"):
                if briefing.get(k) is not None:
                    read[k] = briefing[k]
            by_sym = {i.get("symbol"): i for i in (briefing.get("indices") or [])
                      if isinstance(i, dict)}
            idx_out = []
            for sym in _READ_INDEX_SYMBOLS:
                i = by_sym.get(sym)
                if not i:
                    continue
                spot = m.get(_SPOT_KEY[sym]) or i.get("spot")
                idx_out.append({
                    "symbol": sym, "spot": spot, "flip": i.get("gamma_flip"),
                    "put_wall": i.get("put_wall"), "call_wall": i.get("call_wall"),
                    "max_pain": i.get("max_pain"), "exp_move": i.get("expected_move"),
                    "pc_ratio": i.get("pc_ratio"), "posture": _posture(spot, i.get("gamma_flip")),
                    "what_if": i.get("what_if")})
            if idx_out:
                read["indices"] = idx_out
        if dash.get("breadth_spread") is not None:
            read["breadth_spread"] = dash["breadth_spread"]
        if dash.get("risk"):
            read["risk"] = dash["risk"]
        if sent:
            if sent.get("score") is not None:
                read["sentiment_score"] = sent["score"]
            if sent.get("bias"):
                read["sentiment_bias"] = sent["bias"]
        if not read:
            return {}
        read["summary"] = _market_read_summary(read)
        return read
    except Exception:  # noqa: BLE001 — context is best-effort; never block a cycle.
        return {}
```

In `build_packet`, right after the `if ms_line: packet["market_state"] = ms_line` block
and before `return packet`:

```python
    # Additive REASONING CONTEXT: the market read (gamma briefing + dashboard breadth +
    # sentiment), if any source is present. Never filters the menu (computed above).
    mr = _market_read(market)
    if mr:
        packet["market_read"] = mr
```

**Step 4: Run to verify pass** — plus the full `test_compute_packet.py` (existing tests
green: absent-market → no `market_read`).

**Step 5: Commit** — `feat(driver): assemble market_read into the decision packet`

---

### Task 5: decider `_SYSTEM` market-read guidance

**Files:**
- Modify: `services/driver_svc/decider.py:157` (`_SYSTEM`)
- Test: `services/driver_svc/tests/test_decider.py`

**Step 1: Write the failing test** — append to `test_decider.py`:

```python
def test_system_prompt_has_market_read_guidance():
    from services.driver_svc import decider
    sp = decider.system_prompt()
    assert "market_read" in sp
    assert "put wall" in sp and "call wall" in sp
    # framed to sharpen selection, NOT add caution (keeps the aggressive mandate)
    assert "not" in sp.lower() and "stand down" in sp.lower()
```

**Step 2: Run to verify it fails.**

**Step 3: Implement** — append this sentence to the `_SYSTEM` string (inside the literal,
before the final `"Call submit_decision exactly once."`):

```python
    "A market_read may be present (gamma structure, breadth, sentiment). Use it to sharpen "
    "strike/side selection and conviction — prefer put-credit shorts below the put wall and "
    "call-credit shorts above the call wall; treat spot below the gamma flip (negative gamma) "
    "or risk-off / falling breadth as a reason to be selective, not to stand down. When the "
    "read is favorable, press toward the target. This is context to improve selection, not a "
    "mandate to trade less. "
```

**Step 4: Run to verify pass** — plus the existing decider suite (the parse/stand-down
tests are unaffected).

**Step 5: Commit** — `feat(driver): decider guidance for the market_read (sharpen selection)`

---

### Task 6: handler reads + `run_autonomous_cycle` wiring

**Files:**
- Modify: `services/driver_svc/handlers.py` (constants near line 45; readers near
  `_read_market_state` ~line 100; wiring in `run_autonomous_cycle` ~line 191)
- Test: `services/driver_svc/tests/test_handlers_autonomous.py`

**Step 1: Write the failing test** — add a test that seeds the caches and asserts the
model-facing packet carried `market_read` (spy on `decider.decide`):

```python
def test_run_autonomous_cycle_threads_market_read(fake_bus, monkeypatch):
    from services.driver_svc import handlers, compute
    handlers.set_control(fake_bus, enabled=True)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:driver_paper_account",
                       {"snapshot": {"session_pnl": 0.0}, "positions": []})
    # Seed the market-read sources.
    fake_bus.cache_set("cache:market:dashboard", {"categories": [{"category": "B", "tiles": [
        {"display": "$ADVN-$DECN", "last": -620.0, "color_state": "risk_off_mild"}]}]})
    fake_bus.cache_set("cache:sentiment:composite",
                       {"live": {"composite": {"total_score": "4.1", "bias": "bearish"}},
                        "derived": {"trend": {}}})
    fake_bus.cache_set("cache:options:gamma_analyze_midday", {
        "slot": "midday", "generated_at": _today_iso() + "T12:30:00-05:00",
        "analysis": {"bias": -35, "regime": "neg gamma", "indices": [
            {"symbol": "$SPX", "gamma_flip": 6005, "put_wall": 5900, "call_wall": 6050}]}})
    monkeypatch.setattr(compute, "fetch_market_context",
                        lambda: {"vix": 14, "spx_spot": 5980.0})
    seen = {}
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: seen.setdefault("pkt", packet) or
                        {"stand_down": True, "trades": []})
    handlers.run_autonomous_cycle(fake_bus)
    mr = seen["pkt"].get("market_read")
    assert mr and mr["breadth_spread"] == -620.0 and mr["sentiment_score"] == 4.1
    assert mr["risk"] == "risk_off" and mr["indices"][0]["symbol"] == "$SPX"
```

Add a `_today_iso()` helper in the test (`datetime.date.today().isoformat()`). If
`test_handlers_autonomous.py` already stubs `compute.run_cycle` wholesale, instead spy on
`compute.run_cycle` to capture the `market` kwarg and assert it carries `briefing` /
`dashboard` / `sentiment`; match the file's existing idiom.

**Step 2: Run to verify it fails.**

**Step 3: Implement** — in `handlers.py`, add constants:

```python
CACHE_MARKET_DASHBOARD = "cache:market:dashboard"
# The four scheduled gamma_analyze slot keys (mirror options_svc CACHE_GAMMA_ANALYZE_SCHED).
GAMMA_ANALYZE_SLOTS = ("premarket", "open", "midday", "close")
CACHE_GAMMA_ANALYZE = {s: f"cache:options:gamma_analyze_{s}" for s in GAMMA_ANALYZE_SLOTS}
```

Add readers near `_read_market_state`:

```python
def _read_briefing(bus, today_ct):
    """Freshest TODAY gamma briefing analysis across the 4 scheduled slot keys (→ None)."""
    try:
        payloads = [_read_payload(bus, k) for k in CACHE_GAMMA_ANALYZE.values()]
        return compute._pick_latest_briefing([p for p in payloads if p], today_ct)
    except Exception:  # noqa: BLE001 — best-effort context.
        return None


def _read_sentiment_magnitude(bus):
    """Sentiment 0-10 score + bias from ``cache:sentiment:composite`` live.composite (→ None)."""
    try:
        comp = ((_read_payload(bus, CACHE_SENTIMENT_COMPOSITE) or {})
                .get("live") or {}).get("composite") or {}
        raw = comp.get("total_score")
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        bias = comp.get("bias")
        return {"score": score, "bias": bias} if (score is not None or bias) else None
    except Exception:  # noqa: BLE001 — best-effort context.
        return None
```

In `run_autonomous_cycle`, after the existing `market_state` merge (~line 196):

```python
    # Additive market-read context (gamma briefing + dashboard breadth + sentiment
    # magnitude) — reasoning context only; NO hard rule (guardrails untouched).
    briefing = _read_briefing(bus, date.today())
    if briefing:
        market["briefing"] = briefing
    dash = _read_payload(bus, CACHE_MARKET_DASHBOARD)
    if dash:
        market["dashboard"] = dash
    sent = _read_sentiment_magnitude(bus)
    if sent:
        market["sentiment"] = sent
```

**Step 4: Run to verify pass** — plus the full `test_handlers_autonomous.py` (existing
seeded cycles unaffected — the new reads degrade to nothing when unseeded).

**Step 5: Commit** — `feat(driver): read gamma briefing + dashboard + sentiment into the cycle`

---

### Task 7: observability (decision-log summary) + e2e + docs

**Files:**
- Modify: `services/driver_svc/compute.py` (`run_cycle` returns `market_read`)
- Modify: `services/driver_svc/handlers.py` (`_publish_autonomous` stamps the summary;
  `run_autonomous_cycle` passes it through)
- Modify: `shared/contracts/driver.py` (docstring note only — rows stay loose)
- Test: `services/driver_svc/tests/test_autonomous_e2e.py`, `test_handlers_autonomous.py`
- Docs: root `CLAUDE.md` (changelog + `/driver` route note); memory

**Step 1: Write the failing tests:**

```python
# test_handlers_autonomous.py — the summary lands on the newest decision-log row
def test_decision_log_carries_market_read_summary(fake_bus, monkeypatch):
    from services.driver_svc import handlers
    handlers._publish_autonomous(
        fake_bus, day_pnl=0.0, positions=[], decision={"day_thesis": "t", "stand_down": True},
        guarded={"rejected": [], "halted": False, "halt_reason": None}, executed=[],
        control={"enabled": True, "halted": False},
        market_read={"summary": "neg gamma · bias -35 · breadth -620 risk_off · sent 4.1"})
    row = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"][0]
    assert "bias -35" in row["market_read"]
```

```python
# test_autonomous_e2e.py — with sources seeded the packet carries market_read; without, it doesn't
def test_autonomous_e2e_packet_has_market_read(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_scan(fake_bus, _QQQ_PCS); _seed_paper(fake_bus, session_pnl=0.0)
    fake_bus.cache_set("cache:market:dashboard", {"categories": [{"category": "B", "tiles": [
        {"display": "$ADVN-$DECN", "last": -620.0, "color_state": "risk_off_mild"}]}]})
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    seen = {}
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: seen.setdefault("pkt", packet) or
                        {"stand_down": True, "trades": []})
    handlers.run_autonomous_cycle(fake_bus)
    assert seen["pkt"]["market_read"]["risk"] == "risk_off"
    # And the /driver log row shows the summary.
    row = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"][0]
    assert row.get("market_read")
```

**Step 2: Run to verify they fail.**

**Step 3: Implement:**
- `compute.run_cycle` success return — add `"market_read": packet.get("market_read")`.
- `handlers._publish_autonomous` — add param `market_read=None`; in the `log.insert(0, {...})`
  row add `"market_read": (market_read or {}).get("summary") if isinstance(market_read, dict) else None`.
- `handlers.run_autonomous_cycle` — pass `market_read=out.get("market_read")` into the
  `_publish_autonomous(...)` call.
- `shared/contracts/driver.py` `AutonomousState` docstring — note each `decisions[]` row may
  carry a `market_read` one-line summary (rows stay loose dicts; no schema change).

**Step 4: Run to verify pass** — then the FULL driver suite + contracts:
```
.venv\Scripts\python -m pytest services\driver_svc -q
.venv\Scripts\python -m pytest shared\contracts -q
```
Both green (existing e2e clamp/halt assertions still pass).

**Step 5: Docs + memory, then commit:**
- Root `CLAUDE.md`: prepend a 2026-07-08 changelog entry (the market-context block: what
  it feeds, the sourcing = briefing + live spot, context-only / guardrail deferred, the
  restart note) and add a sentence to the `/driver` route row.
- Consider a memory note (feedback/project) if a non-obvious lesson emerged.
- Commit — `feat(driver): surface market_read summary on the decision log + e2e + docs`

---

## Final review

After all tasks: dispatch a final code-reviewer over the whole change (spec compliance +
quality), confirm `services\driver_svc` + `shared\contracts` suites green, then use
superpowers:finishing-a-development-branch.

**Restart note (for the user):** restart `driver_svc` to load this; it benefits from
`options_svc` + `market_svc` + `sentiment_svc` running so the briefing / dashboard /
composite caches are populated. PAPER ONLY.

## Deferred (NOT in this plan)
- ③ Wall-aware guardrail + breadth halt (needs a backtest before it can gate).
- ② Scanner / Swing `rr_delta` + breadth tilts; `regime_filter` bridge→cache consistency fix.
