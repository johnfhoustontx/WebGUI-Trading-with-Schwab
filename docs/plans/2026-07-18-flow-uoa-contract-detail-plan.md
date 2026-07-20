# Flow UOA contract-detail Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the aggregate volume-spike flow alert with a per-contract unusual-options-activity (vol/OI) detector that names the specific option and includes Strike / Cost / Expiry / Premium; enrich the crossover alert with premium amounts.

**Architecture:** A pure `flow_alerts.detect_uoa(symbol, chain, cfg)` runs inside the 1-min GEX poll's existing `on_chain` hook (reusing the already-fetched chain — no re-fetch), stashing qualifying contracts per symbol in a consume-once module dict; `handlers.run_flow_alerts` consumes the stash, dedups per contract (once/day), and pushes/publishes exactly as today. The crossover detector is unchanged; the aggregate spike detector is removed.

**Tech Stack:** Python 3.11, options_svc, `gex_collector.poll_once(on_chain=…)`, pytest + fakeredis.

**Design:** [2026-07-18-flow-uoa-contract-detail-design.md](2026-07-18-flow-uoa-contract-detail-design.md)

**Key facts:**
- Chain maps: `chain["callExpDateMap"]` / `["putExpDateMap"]` = `{"YYYY-MM-DD:dte": {"<strike>": [contract, …], …}, …}`. Contract has `totalVolume`, `openInterest`, `mark`/`bid`/`ask`.
- `flow_alerts.py` is PURE (stdlib + repo_paths only) — inline a small mark helper, do NOT import the engine.
- The poll: `compute.collect_gex_snapshots(capture_symbols=None)` calls `gc.poll_once(..., on_chain=on_chain)`; today `on_chain` only fires for `capture_symbols` (gamma stash). Extend it to ALWAYS run UOA + stash.
- `handlers.run_flow_alerts` (from the prior feature) reads the aggregate flow series (crossover) + a date-scoped Redis cooldown map, pushes via `push_notify.send_flow_alert`, appends to `cache:options:flow_alerts`.

**Test commands:** `.venv\Scripts\python -m pytest services\options_svc\tests\<file> -q`.

---

### Task 1: Pure `detect_uoa` + text + config; remove the aggregate spike

**Files:** Modify `services/options_svc/flow_alerts.py`, `config/flow_alerts.toml`, `services/options_svc/tests/test_flow_alerts.py`.

**Step 1: Write failing tests** (append to `test_flow_alerts.py`):
```python
def _chain_uoa():
    exp = "2026-07-18:2"   # dated
    z = "2026-07-15:0"     # 0DTE
    def c(strike, vol, oi, mark):
        return {"totalVolume": vol, "openInterest": oi, "mark": mark}
    return {"underlyingPrice": 450.0,
            "callExpDateMap": {
                exp: {"450.0": [c(450.0, 8200, 1300, 1.85)],   # 6.3x OI, $1.52M — qualifies
                      "460.0": [c(460.0, 100, 50, 0.20)]},     # tiny — below floors
                z:   {"451.0": [c(451.0, 9000, 0, 0.50)]}},    # oi=0 → skipped
            "putExpDateMap": {
                exp: {"440.0": [c(440.0, 6000, 900, 2.10)]}}}  # 6.7x OI, $1.26M — qualifies


def test_detect_uoa_qualifies_and_extracts_fields():
    cfg = {"uoa": {"k": 3.0, "vol_floor": 500, "premium_floor": 250000, "top_n": 3}}
    out = flow_alerts.detect_uoa("SPY", _chain_uoa(), cfg)
    ids = {(a["side"], a["strike"], a["expiry"]) for a in out}
    assert ("call", 450.0, "2026-07-18") in ids
    assert ("put", 440.0, "2026-07-18") in ids
    # the tiny 460 (below vol/premium floor) and the oi=0 451 are excluded
    assert all(not (a["strike"] == 460.0 or a["oi"] == 0) for a in out)
    a = next(a for a in out if a["strike"] == 450.0)
    assert a["type"] == "uoa" and a["symbol"] == "SPY" and a["dte"] == 2
    assert a["cost"] == 1.85 and a["volume"] == 8200 and a["oi"] == 1300
    assert round(a["vol_oi"], 1) == 6.3 and round(a["premium"]) == 1517000


def test_detect_uoa_top_n_by_premium():
    # 4 qualifiers, top_n=2 → only the 2 richest by premium survive.
    exp = "2026-07-18:2"
    def c(v, oi, m): return {"totalVolume": v, "openInterest": oi, "mark": m}
    chain = {"callExpDateMap": {exp: {
        "1.0": [c(1000, 100, 1.0)], "2.0": [c(1000, 100, 2.0)],
        "3.0": [c(1000, 100, 3.0)], "4.0": [c(1000, 100, 4.0)]}}, "putExpDateMap": {}}
    cfg = {"uoa": {"k": 3.0, "vol_floor": 500, "premium_floor": 1, "top_n": 2}}
    out = flow_alerts.detect_uoa("X", chain, cfg)
    assert sorted(a["strike"] for a in out) == [3.0, 4.0]   # richest premiums


def test_alert_text_uoa_has_all_fields():
    a = {"type": "uoa", "side": "call", "symbol": "SPY", "strike": 450.0,
         "expiry": "2026-07-18", "dte": 2, "cost": 1.85, "volume": 8200,
         "oi": 1300, "vol_oi": 6.3, "premium": 1517000.0}
    t = flow_alerts.alert_text(a)
    assert "SPY" in t and "07/18" in t and "450" in t and "C" in t
    assert "$1.85" in t and "8,200" in t and "1,300" in t and "6.3" in t
    assert "1.5M" in t or "1.52M" in t   # premium humanized


def test_alert_text_uoa_0dte_tag():
    a = {"type": "uoa", "side": "put", "symbol": "SPY", "strike": 451.0,
         "expiry": "2026-07-15", "dte": 0, "cost": 0.5, "volume": 9000,
         "oi": 400, "vol_oi": 22.5, "premium": 450000.0}
    assert "0DTE" in flow_alerts.alert_text(a)


def test_alert_text_crossover_shows_premiums():
    a = {"type": "crossover", "side": "calls_over", "symbol": "$SPX",
         "call_prem": 2100000.0, "put_prem": 1950000.0}
    t = flow_alerts.alert_text(a)
    assert "$SPX" in t and ("2.1M" in t or "2.10M" in t) and ("1.9M" in t or "1.95M" in t)
    assert "bullish" in t.lower()
```
Also **DELETE the now-obsolete spike tests**: `test_spike_fires_above_baseline_and_floor`, `test_spike_respects_floor`, `test_spike_warmup_needs_min_points`, `test_spike_dead_quiet_name_needs_k_times_min_baseline` (the aggregate spike is being removed). Keep all crossover tests + `test_detect_flow_alerts_cooldown_suppresses_repeat` (crossover-based) + the loader tests.

Run `.venv\Scripts\python -m pytest services\options_svc\tests\test_flow_alerts.py -q` → the new tests FAIL, and the removed-spike tests are gone.

**Step 2: Implement in `flow_alerts.py`.**

Add a mark helper + `detect_uoa` + humanizer + the UOA/crossover `alert_text` branches:
```python
def _mark(c):
    """Contract mark (mid) = mark field, else (bid+ask)/2. None if unusable."""
    for key in ("mark",):
        v = c.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    bid, ask = c.get("bid"), c.get("ask")
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and (bid + ask) > 0:
        return (bid + ask) / 2.0
    return None


def detect_uoa(symbol, chain, cfg):
    """Contract-level unusual options activity for one symbol from a live chain.

    Qualify a contract when volume/open-interest >= k AND volume >= vol_floor AND
    premium ($ = mark*vol*100) >= premium_floor; skip oi <= 0 (ratio undefined).
    Return the top_n qualifiers by premium (desc). Pure + defensive → []."""
    u = (cfg or {}).get("uoa", {})
    k = u.get("k", 3.0); vol_floor = u.get("vol_floor", 500)
    prem_floor = u.get("premium_floor", 250000); top_n = u.get("top_n", 3)
    out = []
    try:
        for side, mapkey in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            exp_map = (chain or {}).get(mapkey) or {}
            if not isinstance(exp_map, dict):
                continue
            for exp_key, strike_map in exp_map.items():
                expiry = str(exp_key).split(":")[0]
                try:
                    dte = int(str(exp_key).split(":")[1])
                except (IndexError, ValueError):
                    dte = None
                for strike_str, contracts in (strike_map or {}).items():
                    try:
                        strike = float(strike_str)
                    except (TypeError, ValueError):
                        continue
                    for c in (contracts or []):
                        vol = c.get("totalVolume") or 0
                        oi = c.get("openInterest") or 0
                        mark = _mark(c)
                        if oi <= 0 or vol < vol_floor or mark is None:
                            continue
                        ratio = vol / oi
                        premium = mark * vol * 100
                        if ratio < k or premium < prem_floor:
                            continue
                        out.append({"type": "uoa", "side": side, "symbol": symbol,
                                    "strike": strike, "expiry": expiry, "dte": dte,
                                    "cost": mark, "volume": int(vol), "oi": int(oi),
                                    "vol_oi": ratio, "premium": premium})
        out.sort(key=lambda a: a["premium"], reverse=True)
        return out[:top_n]
    except Exception:
        log.debug("detect_uoa failed for %s", symbol, exc_info=True)
        return []


def _human_money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "$0"
    a = abs(v)
    if a >= 1e6:
        return f"${v/1e6:.2f}M"
    if a >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:,.0f}"


def _exp_short(expiry, dte):
    if dte == 0:
        return "0DTE"
    try:
        y, m, d = str(expiry).split("-")
        return f"{int(m):02d}/{int(d):02d}"
    except Exception:
        return str(expiry)
```
Update `alert_text` — add UOA + enrich crossover; REMOVE the spike branch:
```python
def alert_text(a) -> str:
    s = a["symbol"]
    if a["type"] == "crossover":
        cp, pp = _human_money(a.get("call_prem")), _human_money(a.get("put_prem"))
        if a["side"] == "calls_over":
            return f"{s} — call premium overtook puts: {cp} calls vs {pp} puts (bullish flip)"
        return f"{s} — put premium overtook calls: {pp} puts vs {cp} calls (bearish flip)"
    if a["type"] == "uoa":
        cp = "C" if a["side"] == "call" else "P"
        return (f"{s} {_exp_short(a.get('expiry'), a.get('dte'))} {a['strike']:g}{cp} — "
                f"UNUSUAL: {a['volume']:,} vol vs {a['oi']:,} OI ({a['vol_oi']:.1f}×) · "
                f"${a['cost']:.2f} · {_human_money(a['premium'])} premium")
    return f"{s}: flow alert"
```
In `detect_flow_alerts`, DELETE the spike loop (the `for side in ("call","put"):` block that calls `_spike_rows`) and the `sp = cfg.get("spike", {})` line — leave only the crossover pass. DELETE `detect_spike`, `_spike_rows`, `_increments` (now unused).

**Step 3: Config** — in `config/flow_alerts.toml` replace the whole `[spike]` block with:
```toml
[uoa]
k = 3.0                 # flag when volume >= k x open interest
vol_floor = 500         # min contract volume
premium_floor = 250000  # min $ premium (mark x vol x 100) — real money only
top_n = 3               # most-significant contracts per symbol per tick (by premium)
```
And in `flow_alerts.py` `_DEFAULTS`, replace the `"spike": {...}` entry with `"uoa": {"k": 3.0, "vol_floor": 500, "premium_floor": 250000, "top_n": 3}`.

**Step 4: Run** `.venv\Scripts\python -m pytest services\options_svc\tests\test_flow_alerts.py -q` → ALL pass.

**Step 5: Commit** ONLY `flow_alerts.py`, `config/flow_alerts.toml`, `test_flow_alerts.py`:
`feat(flow-alerts): contract-level UOA detector (vol/OI) + premium-rich text; retire aggregate spike`

---

### Task 2: Wire UOA through the poll → handler

**Files:** Modify `services/options_svc/compute.py` (on_chain + stash), `services/options_svc/handlers.py` (`run_flow_alerts` UOA path), Test `services/options_svc/tests/test_handlers.py`.

**Step 1: Write the failing handler test** (append):
```python
def test_run_flow_alerts_emits_uoa_from_stash(monkeypatch):
    from shared.bus import Bus
    from services.options_svc import handlers, compute
    bus = Bus(fake=True)
    contract = {"type": "uoa", "side": "call", "symbol": "SPY", "strike": 450.0,
                "expiry": "2026-07-18", "dte": 2, "cost": 1.85, "volume": 8200,
                "oi": 1300, "vol_oi": 6.3, "premium": 1517000.0}
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: [])   # no crossover symbols
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {"SPY": [dict(contract)]})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1000)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))
    handlers.run_flow_alerts(bus)
    env = bus.cache_get("cache:options:flow_alerts")
    ids = [a["id"] for a in env.payload["alerts"]]
    assert ids == ["SPY|uoa|call|450|2026-07-18"] and len(sent) == 1
    assert "07/18" in env.payload["alerts"][0]["text"]
    # Same contract next tick → once-per-day dedup, no re-push/re-append.
    monkeypatch.setattr(compute, "take_uoa_stash", lambda: {"SPY": [dict(contract)]})
    handlers.run_flow_alerts(bus)
    assert len(sent) == 1 and len(bus.cache_get("cache:options:flow_alerts").payload["alerts"]) == 1
```
Run → FAIL.

**Step 2: Implement the stash in `compute.py`.**
Add near the other tick-stash helpers (`_stash_tick_chain`):
```python
_UOA_STASH: dict = {}   # {symbol: [uoa contract dicts]} for the current tick (consume-once)


def clear_uoa_stash():
    _UOA_STASH.clear()


def stash_uoa(symbol, contracts):
    if contracts:
        _UOA_STASH[symbol] = contracts


def take_uoa_stash() -> dict:
    """Return + clear the tick's UOA results (consumed once by run_flow_alerts)."""
    out = dict(_UOA_STASH)
    _UOA_STASH.clear()
    return out
```
In `collect_gex_snapshots`, import `flow_alerts` (lazy, at the top of the function: `from services.options_svc import flow_alerts`), load thresholds once, clear the stash, and make `on_chain` ALWAYS run UOA (plus the existing capture stash):
```python
        from services.options_svc import flow_alerts
        clear_uoa_stash()
        _uoa_cfg = flow_alerts.load_thresholds()
        wanted = set(capture_symbols) if capture_symbols else set()

        def on_chain(sym, chain):  # noqa: F811
            if sym in wanted:
                _stash_tick_chain(sym, chain)
            try:
                stash_uoa(sym, flow_alerts.detect_uoa(sym, chain, _uoa_cfg))
            except Exception:
                gc.log.debug("UOA detect failed for %s", sym, exc_info=True)

        gc.poll_once(_proxy.schwab_py_client, gt.GammaEngine(), conn, on_chain=on_chain)
```
(Replace the current `on_chain = None; if capture_symbols: …` block with the above — `on_chain` is now always defined.)

**Step 3: Implement the UOA path in `handlers.run_flow_alerts`.**
After the crossover loop builds `fresh` (from `detect_flow_alerts` over the symbols), add the UOA pass — once-per-day dedup via the SAME cooldown map (membership, not time, since vol/OI is monotonic):
```python
        # Contract-level UOA (from the poll's on_chain stash — no re-fetch). Once per
        # contract per day: the cooldown map doubles as a date-scoped seen-set.
        for sym, contracts in compute.take_uoa_stash().items():
            for c in contracts:
                cid = f"{sym}|uoa|{c['side']}|{c['strike']:g}|{c['expiry']}"
                if cid in cooldowns:
                    continue
                cooldowns[cid] = now_ts
                a = dict(c)
                a["id"] = cid
                a["text"] = flow_alerts.alert_text(a)
                fresh.append(a)
```
(`flow_alerts` is already imported in handlers; `compute` is imported. `cooldowns`/`fresh`/`now_ts` are already in scope in `run_flow_alerts`.) The rest (push loop + deduped append to `cache:options:flow_alerts` + cooldown persist) is unchanged and already handles the new alerts.

**Step 4: Run:**
- `.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -q -k flow` → pass (crossover + new UOA test).
- `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -q -k "collect or gex or uoa"` → no regression (collect_gex still works; the on_chain change is additive/guarded).

**Step 5: Commit** ONLY `compute.py`, `handlers.py`, `test_handlers.py`:
`feat(flow-alerts): compute UOA in the poll on_chain + emit contract alerts in run_flow_alerts`

---

### Task 3: Docs + verification

**Step 1:** Restart `options_svc`. During RTH, verify via Redis: read `cache:options:flow_alerts` after a few ticks and confirm UOA alerts carry strike/expiry/cost/premium; or seed a synthetic chain with a high-vol/OI contract into the poll path. Off-hours, confirm no UOA fires (poll window closed). Also verify a Discord/Telegram message (if creds set) shows the contract line.

**Step 2:** Update the root `CLAUDE.md` — a dated "Last updated" entry noting the UOA upgrade (contract-level vol/OI, strike/cost/expiry/premium, retires the aggregate spike; crossover gains premiums), and update the `config/flow_alerts.toml` line (spike keys → uoa keys). Note the desktop-notification diagnosis is not a code change.

**Step 3: Commit** `docs(flow-alerts): record contract-level UOA upgrade`.

**Step 4:** Full suites green: `.venv\Scripts\python -m pytest services\options_svc -q` (expect the 2 pre-existing `test_expected_move` date fails only) and `cd webgui && ..\.venv\Scripts\python -m pytest -q` (unchanged — the webgui just renders the richer `text`).

---

## Notes for the implementer
- **DRY:** reuse the poll's `on_chain` (no new chain fetch), the existing `run_flow_alerts` push/publish/cooldown machinery, and `alert_text` for both channels.
- **YAGNI:** no per-contract history, no crossover strike/expiry, no re-fetch.
- **Defensive:** `detect_uoa` returns `[]` on any failure; the on_chain UOA is guarded so it can't break collection; `run_flow_alerts` stays fully guarded.
- **Dedup model:** crossover keeps the 30-min TIME cooldown (it can flip back); UOA uses once-per-day MEMBERSHIP (vol/OI is monotonic — a contract crosses K once and stays, so alert once). Both share the one date-scoped cooldown map.
- **Purity:** `flow_alerts.py` stays stdlib+repo_paths only (inline `_mark`; do not import the engine).
