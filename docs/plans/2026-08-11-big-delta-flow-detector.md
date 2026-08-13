# `big_delta` flow detector — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> subagent-driven-development) to implement this plan task-by-task.

**Goal:** Add a fourth options-flow detector that fires on relative delta-notional
(directional exposure changing hands), shipping quiet-live (Flow screen, no push), fully
config-driven, with the 15:30 instrumentation kept as the tuning loop.

**Architecture:** A pure `detect_big_delta` in `services/options_svc/flow_alerts.py` runs in the
1-min GEX poll's `on_chain` callback beside `detect_uoa`, stashing qualifiers that
`handlers.run_flow_alerts` drains into `cache:options:flow_alerts` — but skips the phone-push /
webgui-chime while `[big_delta].push` is false. The instrumentation script reads the same
`[big_delta]` config so calibration and the live detector never drift.

**Tech Stack:** Python 3.11, options_svc (FastAPI), NiceGUI webgui, `config/flow_alerts.toml`,
Redis (fakeredis under pytest). Design: `docs/plans/2026-08-11-big-delta-flow-detector-design.md`.

---

## Environment (read first)

- Worktree: `D:\WebGUI Trading with Schwab\.worktrees\big-delta-detector` (branch
  `claude/big-delta-detector`). Edit + test only here.
- No `.venv` in the worktree — use the absolute dev venv:
  `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`.
- A bare `cd` into a subdir breaks the relative-path hooks — run tests in a **subshell**
  `(cd "<wt>/<app>" && "<venv>" -m pytest ...)`; use `git -C "<wt>"` for git. Keep the shell at the
  dev root.
- Per-folder test runs only (never `pytest services` over all services).
- **Baselines to compare the failing SET against** (never the count): options_svc **2** failed
  (`test_expected_move` date-relative), options-scanner **11** (gex_collector/key_levels_doc/
  TestEarningsAvoidance), webgui **0**.
- Commit after each task with `git -C "<wt>"`, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: `[big_delta]` config + `load_thresholds` defaults

**Files:**
- Modify: `config/flow_alerts.toml` (add the `[big_delta]` block)
- Modify: `services/options_svc/flow_alerts.py:17` (add `big_delta` to the `_DEFAULTS` literal in
  `load_thresholds`)
- Test: `services/options_svc/tests/test_flow_alerts.py`

**Step 1 — failing test:**
```python
def test_load_thresholds_has_big_delta_defaults(monkeypatch, tmp_path):
    # With NO toml, defaults are present and sane.
    monkeypatch.setattr(flow_alerts, "_THRESHOLDS_PATH", tmp_path / "missing.toml")
    flow_alerts._reset_thresholds_cache()  # if a cache exists; else skip
    cfg = flow_alerts.load_thresholds()
    bd = cfg["big_delta"]
    assert bd["enabled"] is True and bd["push"] is False
    assert bd["rel_threshold"] == 0.20
    assert bd["min_contract_notional"] == 10_000_000
    assert bd["delta_lo"] == 0.05 and bd["delta_hi"] == 0.85 and bd["delta_max"] == 1.0
    assert bd["top_n"] == 3
```
(Check how `load_thresholds` is currently tested / how the path + mtime cache is monkeypatched —
mirror it. If there's no reset hook, test the merge with a written toml instead.)

**Step 2 — verify fail:** `(cd "<wt>" && "<venv>" -m pytest services/options_svc/tests/test_flow_alerts.py -k big_delta_defaults -v)` → FAIL (KeyError `big_delta`).

**Step 3 — implement:** add to the `_DEFAULTS` dict literal at `flow_alerts.py:17` (beside `"uoa": {...}`):
```python
"big_delta": {"enabled": True, "push": False, "rel_threshold": 0.20,
              "min_contract_notional": 10_000_000, "delta_lo": 0.05, "delta_hi": 0.85,
              "delta_max": 1.0, "top_n": 3},
```
And add the block to `config/flow_alerts.toml` (see the design doc for the annotated version). The
merge in `load_thresholds` is table-by-table already (`[big_delta]` overrides individual keys).

**Step 4 — verify pass.** **Step 5 — commit** (`feat(flow): [big_delta] config + defaults`).

---

## Task 2: `detect_big_delta` (the detector)

**Files:**
- Modify: `services/options_svc/flow_alerts.py` (new function beside `detect_uoa`)
- Test: `services/options_svc/tests/test_flow_alerts.py`

**Step 1 — failing tests** (write a small chain-builder helper mirroring `detect_uoa`'s tests):
```python
def _chain(spot, contracts):
    # contracts: list of (side, strike, expiry, dte, delta, vol)
    m = {"call": {}, "put": {}}
    for side, strike, expiry, dte, delta, vol in contracts:
        m[side].setdefault(f"{expiry}:{dte}", {}).setdefault(f"{strike}", []).append(
            {"delta": delta, "totalVolume": vol, "mark": 1.0})
    return {"underlyingPrice": spot, "callExpDateMap": m["call"], "putExpDateMap": m["put"]}

_CFG = {"big_delta": {"enabled": True, "rel_threshold": 0.20,
        "min_contract_notional": 10_000_000, "delta_lo": 0.05, "delta_hi": 0.85,
        "delta_max": 1.0, "top_n": 3}}

def test_big_delta_fires_top_share_not_sub_share():
    # A carries 60% of gross (fires), B ~13% each of the rest (below 20% -> no).
    # deltas 0.5; vols chosen so A dominates; spot 100 -> notional |d|*vol*100*spot.
    ch = _chain(100.0, [
        ("call", 100, "2026-08-14", 3, 0.5, 300_000),   # A: |0.5|*300k*100*100 = $1.5B
        ("call", 101, "2026-08-14", 3, 0.5, 50_000),    # B: $250M
        ("call", 102, "2026-08-14", 3, 0.5, 50_000),    # C: $250M
    ])
    out = flow_alerts.detect_big_delta("SPY", ch, _CFG)
    fired = {a["strike"] for a in out}
    assert 100 in fired               # 1.5B / 2.0B = 75% >= 20% and >= $10M
    assert 101 not in fired and 102 not in fired  # 250M / 2.0B = 12.5% < 20%

def test_big_delta_abs_floor_drops_tiny_name():
    # One contract = 100% of a tiny gross but only $2M notional -> below the $10M floor.
    ch = _chain(50.0, [("put", 20, "2026-08-14", 3, 0.4, 1000)])  # 0.4*1000*100*50 = $2M
    assert flow_alerts.detect_big_delta("XLC", ch, _CFG) == []

def test_big_delta_drops_sentinel_and_band():
    ch = _chain(100.0, [
        ("call", 100, "2026-08-14", 3, -999.0, 900_000),  # sentinel |d|>1 -> excluded from gross+fire
        ("call", 101, "2026-08-14", 3, 0.95, 900_000),    # deep-ITM > delta_hi -> excluded
        ("call", 102, "2026-08-14", 3, 0.01, 900_000),    # near-zero < delta_lo -> excluded
        ("call", 103, "2026-08-14", 3, 0.5, 300_000),     # only real contract -> 100% of gross, $1.5B
    ])
    out = flow_alerts.detect_big_delta("SPY", ch, _CFG)
    assert [a["strike"] for a in out] == [103]

def test_big_delta_topn_and_pct_of_gross():
    ch = _chain(100.0, [("call", 100+i, "2026-08-14", 3, 0.5, 200_000) for i in range(5)])
    out = flow_alerts.detect_big_delta("SPY", ch, {"big_delta": {**_CFG["big_delta"], "top_n": 2}})
    assert len(out) == 2
    assert all(a["type"] == "big_delta" for a in out)
    assert 0 < out[0]["pct_of_gross"] <= 1.0 and out[0]["delta_notional"] >= out[1]["delta_notional"]

def test_big_delta_defensive():
    assert flow_alerts.detect_big_delta("SPY", None, _CFG) == []
    assert flow_alerts.detect_big_delta("SPY", {}, _CFG) == []
```

**Step 2 — verify fail** (function undefined).

**Step 3 — implement** `detect_big_delta` (walk the two exp maps like `detect_uoa`, one accumulation
pass + one filter pass):
```python
def detect_big_delta(symbol, chain, cfg):
    """Relative delta-notional flow: a contract carrying >= rel_threshold of its symbol's OWN
    gross delta-notional AND >= min_contract_notional absolute. Pure + defensive → []."""
    b = (cfg or {}).get("big_delta", {})
    rel = b.get("rel_threshold", 0.20); floor = b.get("min_contract_notional", 10_000_000)
    lo = b.get("delta_lo", 0.05); hi = b.get("delta_hi", 0.85)
    dmax = b.get("delta_max", 1.0); top_n = b.get("top_n", 3)
    try:
        spot = (chain or {}).get("underlyingPrice") or 0
        cand, gross = [], 0.0
        for side, mapkey in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            exp_map = (chain or {}).get(mapkey) or {}
            if not isinstance(exp_map, dict):
                continue
            for exp_key, strike_map in exp_map.items():
                expiry = str(exp_key).split(":")[0]
                try: dte = int(str(exp_key).split(":")[1])
                except (IndexError, ValueError): dte = None
                for strike_str, contracts in (strike_map or {}).items():
                    try: strike = float(strike_str)
                    except (TypeError, ValueError): continue
                    for c in (contracts or []):
                        try:
                            d = c.get("delta")
                            vol = c.get("totalVolume") or 0
                            if d is None or vol <= 0: continue
                            ad = abs(float(d))
                            if ad > dmax or ad < lo or ad > hi: continue
                            dn = ad * vol * 100 * (spot or 0)
                            if dn <= 0: continue
                            gross += dn
                            cand.append({"type": "big_delta", "side": side, "symbol": symbol,
                                         "strike": strike, "expiry": expiry, "dte": dte,
                                         "delta": float(d), "volume": int(vol),
                                         "delta_notional": dn, "cost": _mark(c)})
                        except Exception:
                            continue
        if gross <= 0:
            return []
        thr = rel * gross
        out = [a for a in cand if a["delta_notional"] >= thr and a["delta_notional"] >= floor]
        for a in out:
            a["pct_of_gross"] = a["delta_notional"] / gross
        out.sort(key=lambda a: a["delta_notional"], reverse=True)
        return out[:top_n]
    except Exception:
        log.debug("detect_big_delta failed for %s", symbol, exc_info=True)
        return []
```
Note: relative test is spot-independent (spot cancels), but the `$` floor + display need spot; when
spot is 0/absent `dn` is 0 → nothing fires (acceptable degrade; the design allows it). If a
spot-absent relative-only fire is later wanted, revisit — YAGNI for now.

Also extend `alert_text(a)` (the human string used for the screen/`text` field) to handle
`type=="big_delta"` — e.g. `"SPY big Δ: $312M call · 24% of gross · 100 08/14"` (mirror the UOA
branch; use `_human_money(delta_notional)` + `pct_of_gross`).

**Step 4 — verify pass. Step 5 — commit** (`feat(flow): detect_big_delta relative delta-notional detector`).

---

## Task 3: big_delta stash + `on_chain` wiring (compute)

**Files:**
- Modify: `services/options_svc/compute.py` (near `_UOA_STASH` ~1847; and the `on_chain` callback in
  `collect_gex_snapshots` ~2220)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1 — failing test:**
```python
def test_big_delta_stash_roundtrips_and_clears():
    compute.clear_big_delta_stash()
    compute.stash_big_delta("SPY", [{"type": "big_delta", "strike": 100}])
    got = compute.take_big_delta_stash()
    assert got == {"SPY": [{"type": "big_delta", "strike": 100}]}
    assert compute.take_big_delta_stash() == {}   # take clears
```

**Step 2 — verify fail. Step 3 — implement:** mirror `_UOA_STASH` exactly:
```python
_BIG_DELTA_STASH: dict = {}
def clear_big_delta_stash(): _BIG_DELTA_STASH.clear()
def stash_big_delta(symbol, contracts):
    if contracts: _BIG_DELTA_STASH[symbol] = contracts
def take_big_delta_stash() -> dict:
    out = dict(_BIG_DELTA_STASH); _BIG_DELTA_STASH.clear(); return out
```
In `collect_gex_snapshots`: call `clear_big_delta_stash()` beside `clear_uoa_stash()` (~2213), and
in `on_chain` (~2226) add:
```python
stash_big_delta(sym, flow_alerts.detect_big_delta(sym, chain, _uoa_cfg))
```
(`_uoa_cfg` there is the already-loaded `flow_alerts.load_thresholds()` result — it now carries
`big_delta`; rename the local to `_flow_cfg` if clearer, but reuse the single load — do NOT load
twice.)

**Step 4 — verify pass. Step 5 — commit** (`feat(flow): stash big_delta on the poll's on_chain hook`).

---

## Task 4: drain + quiet-live push skip (handlers)

**Files:**
- Modify: `services/options_svc/handlers.py:run_flow_alerts` (~1120–1144)
- Test: `services/options_svc/tests/test_handlers.py` (or `test_handlers_flow*.py` if it exists)

**Step 1 — failing tests** (mirror the existing UOA drain test; monkeypatch `compute.take_uoa_stash`
→ {}, `compute.take_big_delta_stash` → a fake symbol map, and `push_notify.send_flow_alert` to
record calls):
```python
def test_big_delta_lands_on_screen_but_not_pushed_when_push_false(monkeypatch, fake_bus):
    monkeypatch.setattr(handlers.flow_alerts, "load_thresholds",
        lambda: {"enabled": True, "big_delta": {"enabled": True, "push": False}, ...})
    monkeypatch.setattr(handlers.compute, "take_uoa_stash", lambda: {})
    monkeypatch.setattr(handlers.compute, "take_big_delta_stash",
        lambda: {"SPY": [{"type":"big_delta","side":"call","strike":100.0,"expiry":"2026-08-14",
                          "dte":3,"delta_notional":3.1e8,"pct_of_gross":0.24,"volume":5000}]})
    pushed = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, config=None: pushed.append(a))
    handlers.run_flow_alerts(fake_bus)
    alerts = fake_bus.cache_get(handlers.CACHE_FLOW_ALERTS).payload["alerts"]
    assert any(a["type"] == "big_delta" for a in alerts)          # on the screen
    assert not any(a["type"] == "big_delta" for a in pushed)      # NOT phone-pushed

def test_big_delta_is_pushed_when_push_true(monkeypatch, fake_bus):
    # same, push=True -> big_delta IS in the pushed list
    ...
```
(Study the existing flow-alert handler test to reuse its bus fixture + gamma-flip/crossover
monkeypatches so the run is otherwise empty.)

**Step 2 — verify fail. Step 3 — implement:** in `run_flow_alerts`, after the UOA drain loop, add a
symmetric big_delta drain from `compute.take_big_delta_stash()` (same cooldown seen-set + id
`f"{sym}|big_delta|{c['side']}|{c['strike']:g}|{c['expiry']}"`, stamp `ts`/`text`, append to
`fresh`). Then change the push loop (~1140):
```python
push_bd = bool(cfg.get("big_delta", {}).get("push", False))
for a in fresh:
    if a.get("type") == "big_delta" and not push_bd:
        continue                     # quiet-live: screen only, no phone
    try: push_notify.send_flow_alert(a, config=push_cfg)
    except Exception: log.exception("send_flow_alert degraded")
```
The screen append (CACHE_FLOW_ALERTS) is UNCHANGED — big_delta always lands there.

**Step 4 — verify pass. Step 5 — commit** (`feat(flow): drain big_delta to the screen, quiet-live push gate`).

---

## Task 5: webgui chime exclusion (alerts)

**Files:**
- Modify: `webgui/alerts.py:new_flow_alerts`
- Test: `webgui/tests/test_alerts.py`

**Step 1 — failing test:**
```python
def test_new_flow_alerts_excludes_big_delta_from_chime():
    view = {"alerts": [{"id": "a", "type": "uoa"}, {"id": "b", "type": "big_delta"}]}
    new, acked = alerts.new_flow_alerts(view, set())
    assert [a["id"] for a in new] == ["a"]        # only uoa chimes
    assert acked == {"a", "b"}                    # both marked seen (b never re-considered)
```

**Step 2 — verify fail. Step 3 — implement:** in `new_flow_alerts`, skip appending a dict whose
`type == "big_delta"` to `new` (but STILL add its id to `all_ids` so it's not reconsidered). One
guard in the loop:
```python
if isinstance(a, dict) and a.get("id") and a["id"] not in all_ids:
    all_ids.add(a["id"])
    if a.get("type") == "big_delta":
        continue                      # quiet-live: no chime/toast, still on the screen
    new.append(a)
```

**Step 4 — verify pass. Step 5 — commit** (`feat(flow): exclude big_delta from the webgui chime`).

---

## Task 6: Flow Alerts screen (webgui)

**Files:**
- Modify: `webgui/pages/options/flow.py` (kind filter, `(type,side)` color map, `alert_detail` cell)
- Test: `webgui/tests/test_flow.py` (the page's pure builders)

**Step 1 — failing tests:** the kind-filter set includes `big_delta`; the color-class map returns a
class for `("big_delta","call")`/`("big_delta","put")` (not the neutral fallback); the detail
builder for a big_delta row renders the delta-notional + pct (e.g. contains `"of gross"`).

**Step 2 — verify fail. Step 3 — implement:** add `big_delta` to the KINDS list, add its two
`(type,side)` entries to the finite Tailwind class map (pick a distinct hue, e.g. violet), and
branch `alert_detail` on `type=="big_delta"` to show `f"{_human_money(delta_notional)} · {pct:.0%} of gross"`.
Keep everything Tailwind-first (add the page to `test_no_inline_style.py` coverage if not already).

**Step 4 — verify pass. Step 5 — commit** (`feat(flow): render big_delta on the Flow Alerts screen`).

---

## Task 7: instrumentation — config-aligned + reconciliation

**Files:**
- Modify: `tools/flow_delta_instrumentation.py`
- Test: `tools/tests/test_flow_delta_instrumentation.py` if it exists, else add pure-helper tests
  there (extract the selection + reconciliation as pure functions to test).

**Step 1 — failing tests** (make the new logic pure + testable):
```python
def test_live_config_selection_applies_big_delta_cfg():
    # given per-symbol contracts + a [big_delta] cfg, returns the same set detect_big_delta would.
    ...
def test_big_delta_reconciliation_filters_live_alerts_by_type():
    live = [{"type":"uoa","id":"x"}, {"type":"big_delta","id":"y"}]
    assert instr.live_big_delta_ids(live) == {"y"}
```

**Step 2 — verify fail. Step 3 — implement:**
- Read `[big_delta]` via `flow_alerts.load_thresholds()` (add options_svc to `sys.path` like the
  script already adds OPTIONS_SCANNER/SCHWAB_PROXY).
- Add a **"Live config" line** to the report: apply the exact live `rel_threshold` /
  `min_contract_notional` / band to today's snapshot → N alerts / M symbols.
- Add a **big_delta reconciliation** section: read `cache:options:flow_alerts` (already read for the
  UOA reconciliation), filter `type=="big_delta"`, compare modelled-config vs actually-fired.
- Make the band / `delta_max` read from `[big_delta]` (replace the module `DELTA_BAND`/`DELTA_MAX`
  constants' use in the live-config path; keep the candidate grid constants but allow `--rel`/`--abs`
  CLI overrides via `argparse`).
- **Do NOT** remove the candidate ABS/REL tables and **do NOT** touch/clear the Windows task.

**Step 4 — verify pass. Step 5 — commit** (`feat(flow): instrumentation reads [big_delta] + reconciles live fires`).

---

## Final: full regression + integration + merge

1. Run all three suites in the worktree; confirm each failing SET equals the documented baseline
   (no NEW failures) plus the new tests green:
   - `(cd "<wt>" && "<venv>" -m pytest services/options_svc -q -rf --tb=line)`
   - `(cd "<wt>/webgui" && "<venv>" -m pytest -q -rf --tb=line)`
   - `(cd "<wt>/options-scanner" && "<venv>" -m pytest tests/ -q -rf --tb=no)` (flow_alerts lives
     in options_svc, but `_mark`/imports may touch options-scanner — run it if anything there moved).
2. Dispatch a final holistic code-review subagent over the branch diff.
3. Append a `docs/CHANGELOG.md` entry.
4. FF `Using_Highcharts` + `main`, verify in dev (restart dev options_svc + webgui; confirm boot +
   the Flow screen renders the big_delta filter; optionally seed a fake big_delta alert into
   `cache:options:flow_alerts` on db 1 and confirm it shows but doesn't chime), then hand off the
   promote.

## Notes for the executor

- The single most error-prone spot is Task 4's push gate — the screen append must stay unchanged;
  ONLY the push loop is gated. Prove both directions (push false → not pushed but on screen; push
  true → pushed).
- Reuse the SINGLE `flow_alerts.load_thresholds()` call in `collect_gex_snapshots` for both
  detectors — don't add a second load.
- Everything is behind `[big_delta].enabled`; if a reviewer worries about risk, note the whole
  detector is inert when `enabled=false`, and silent (screen-only) when `push=false` (the default).
