# Calculator — Rate my trade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A RATE MY TRADE button on `/options/calculator` that grades the legs on screen with the Strategy Finder's own scorer and checklist, and shows a BUY / CAUTION / PASS verdict above the shared Trade detail panel in a dialog.

**Architecture:** A new `calc_rate` command on `options_svc` converts the Calculator's legs into a Strategy Finder row (quotes from the already-cached `calc_chain`), scores it with `strategy_scoring.score_all` without the Finder's quality cut, stamps it with `compute.stamp_candidate`, and publishes `cache:options:calc_rating`. The page matches the answer by `request_id`, reads the checklist context off the loop, derives the verdict with a PURE rule module, and paints a dialog. Design: [2026-09-16-calc-rate-my-trade-design.md](2026-09-16-calc-rate-my-trade-design.md).

**Tech Stack:** Python 3.11, NiceGUI, Redis bus (fakeredis in tests), pytest.

**Conventions that bind every task**
- Branch `claude/calc-rate-my-trade` (from `origin/main`). Commit after each task; end messages with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Python: `"/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (a worktree has no venv). Services tests from the repo root, one service at a time; webgui tests from `webgui/`.
- Tailwind-first: no `.style()`; tone classes from a finite map. Never print a zero for a missing reading.
- Never weaken an existing assertion to make a test pass.
- Edit CRLF files with the Edit tool or a script that preserves endings (not `sed` inserting `\n`).

---

### Task 1: The verdict rule (PURE, webgui)

**Files:**
- Create: `webgui/pages/options/rate_trade.py`
- Test: `webgui/tests/test_rate_trade.py`
- Modify: `webgui/tests/test_no_inline_style.py` (add the module if the guard lists files explicitly)

**Step 1: Write the failing tests**

```python
"""The Rate my trade verdict - PURE (design 2026-09-16 section 2)."""
import pytest
from pages.options import rate_trade as R

@pytest.mark.parametrize("grade,state,word", [
    ("Strong", "pos", "BUY"), ("Good", "pos", "BUY"),
    ("Strong", "warn", "CAUTION"), ("Good", "muted", "CAUTION"),
    ("Strong", "neg", "PASS"), ("Good", "neg", "PASS"),
    ("Marginal", "pos", "CAUTION"), ("Marginal", "warn", "PASS"),
    ("Marginal", "muted", "PASS"), ("Marginal", "neg", "PASS"),
    ("Weak", "pos", "PASS"), ("Weak", "warn", "PASS"),
    ("Weak", "muted", "PASS"), ("Weak", "neg", "PASS"),
])
def test_the_verdict_table(grade, state, word):
    assert R.verdict_word(grade, state) == word

def test_no_grade_is_pass_never_caution():
    for g in (None, "", "unscored", "Excellent?"):
        assert R.verdict_word(g, "pos") == "PASS"

def test_an_unknown_checklist_state_counts_as_a_caution():
    assert R.verdict_word("Strong", None) == "CAUTION"
    assert R.verdict_word("Strong", "weird") == "CAUTION"

def test_reasons_name_failed_gates_cautions_blocks_and_unknown_structure():
    row = {"grade": "Weak", "grade_reason": "Fails: liquidity, PoP",
           "structure_known": False, "vol_gate_blocks": True}
    items = [{"key": "earnings", "label": "Earnings", "tone": "warn", "text": "reports Oct 2"},
             {"key": "book", "label": "Paper book", "tone": "neg", "text": "over the symbol cap"},
             {"key": "vol", "label": "Vol rank", "tone": "pos", "text": "ok"}]
    reasons = R.reasons(row, items)
    assert "Fails: liquidity, PoP" in reasons
    assert "Paper book: over the symbol cap" in reasons
    assert "Earnings: reports Oct 2" in reasons
    assert any("custom structure" in r for r in reasons)
    assert any("volatility" in r.lower() for r in reasons)
    assert not any(r.startswith("Vol rank") for r in reasons)      # a clear line is no reason

def test_banner_view_carries_word_tone_grade_and_score_never_a_zero():
    v = R.banner_view({"grade": "Good", "composite_score": 71.24}, "pos", [])
    assert v["word"] == "BUY" and v["tone"] == R.WORD_TONE["BUY"]
    assert v["grade"] == "Good" and v["score"] == "71"
    blank = R.banner_view({}, "pos", [])
    assert blank["word"] == "PASS" and blank["score"] == "—" and blank["grade"] == "—"

def test_word_tones_are_a_finite_static_map():
    assert set(R.WORD_TONE) == {"BUY", "CAUTION", "PASS"}
    assert all(" " not in c.split("-", 1)[0] for c in R.WORD_TONE.values())
```

**Step 2:** `cd webgui && ../.venv…python -m pytest tests/test_rate_trade.py -q` → FAIL (module missing).

**Step 3: Implement** `rate_trade.py`:
- `WORD_TONE = {"BUY": "text-emerald-400", "CAUTION": "text-amber-400", "PASS": "text-rose-400"}`
- `_GOOD = {"Strong", "Good"}`
- `verdict_word(grade, state)`: `neg` or grade not in `_GOOD | {"Marginal"}` → PASS; grade in `_GOOD` → BUY if `state == "pos"` else CAUTION; Marginal → CAUTION if `state == "pos"` else PASS.
- `reasons(row, items)`: `grade_reason` when it starts with `"Fails:"`; each `neg` then `warn` item as `f"{label}: {text}"`; `"Checks that could not run count as cautions"` when any `muted`; `"A custom structure - judged against the debit bars"` when `structure_known is False`; `"Premium this cheap is refused by the volatility floor on scanned trades"` when `vol_gate_blocks`.
- `banner_view(row, state, items)` → `{word, tone, grade, score, reasons}`; score `f"{round(x):d}"` via `fmt.num`, else `"—"`; grade `"—"` when absent.

**Step 4:** tests PASS. **Step 5:** commit `feat(calc): the Rate my trade verdict rule`.

---

### Task 2: Calculator legs → Strategy Finder legs (service, mostly pure)

**Files:**
- Create: `services/options_svc/rate_trade.py`
- Test: `services/options_svc/tests/test_rate_trade.py`

**Step 1: Write the failing tests** (fixture chain in the thinned `calc_chain` shape):

```python
from services.options_svc import compute  # puts options-scanner on sys.path
from services.options_svc import rate_trade as RT
import strategy_scoring as ssc

EXP = "2026-10-16"
def _c(mark, delta, **kw):
    return [dict({"mark": mark, "bid": mark - .05, "ask": mark + .05, "delta": delta,
                  "gamma": .01, "theta": -.02, "vega": .1, "volatility": 25.0,
                  "openInterest": 900, "totalVolume": 120}, **kw)]
CHAIN = {"callExpDateMap": {f"{EXP}:30": {"105.0": _c(1.2, .30)}},
         "putExpDateMap": {f"{EXP}:30": {"95.0": _c(1.5, -.25), "90.0": _c(.6, -.12)}}}
PCS = [{"option_type": "put", "side": "short", "strike": 95.0, "expiry": EXP, "qty": 3, "premium": 1.55},
       {"option_type": "put", "side": "long", "strike": 90.0, "expiry": EXP, "qty": 3, "premium": None}]

def test_every_calculator_template_maps_to_a_scorer_type():
    for code, (stype, family, label, bias) in RT.CALC_TO_SCORER.items():
        assert stype in ssc._TYPE_PROFILE, code
        assert family and label and bias in ("bullish", "bearish", "neutral")

def test_legs_take_quotes_from_the_chain_and_the_price_from_the_page():
    legs, err = RT.finder_legs(PCS, CHAIN, spot=100.0)
    assert err is None
    short, long_ = legs
    assert (short["kind"], short["side"], short["strike"], short["expiration"]) == ("put", "short", 95.0, EXP)
    assert short["mark"] == 1.55 and long_["mark"] == 0.6        # page price, else chain mark
    assert short["iv"] == 25.0 and short["oi"] == 900 and short["delta"] == -.25

def test_quantities_reduce_to_the_structures_ratio():
    legs, _ = RT.finder_legs(PCS, CHAIN, spot=100.0)
    assert [l["qty"] for l in legs] == [1, 1]
    fly = [dict(PCS[0], qty=2), dict(PCS[1], qty=4)]
    assert [l["qty"] for l in RT.finder_legs(fly, CHAIN, 100.0)[0]] == [1, 2]

def test_a_share_leg_is_one_lot_at_the_pages_price():
    legs, err = RT.finder_legs([{"option_type": "stock", "side": "long", "strike": None,
                                 "expiry": None, "qty": 1, "premium": 99.5}], CHAIN, 100.0)
    assert err is None and legs[0]["kind"] == "stock" and legs[0]["mark"] == 99.5

def test_a_contract_the_chain_lacks_refuses_by_name():
    legs, err = RT.finder_legs([dict(PCS[0], strike=80.0)], CHAIN, 100.0)
    assert legs is None and "80 put" in err and "Oct 16" in err

def test_structure_lookup_keeps_custom_honest():
    known = RT.structure_meta("NAKED_PUT", net_delta=.3)
    assert known == ("SHORT_PUT", "DIRECTIONAL", "Short Put", "bullish", True)
    custom = RT.structure_meta("CUSTOM", net_delta=-.4)
    assert custom[0] == "CUSTOM" and custom[3] == "bearish" and custom[4] is False
```

**Step 2:** `python -m pytest services/options_svc/tests/test_rate_trade.py -q` → FAIL.

**Step 3: Implement** `services/options_svc/rate_trade.py`:
- `CALC_TO_SCORER` — every `webgui/pages/options/strategies.STRATEGY_TEMPLATES` code → `(type, family, label, bias)`, mirroring the builders in `strategy_scanner` (`_assemble` call sites): LONG_CALL/LONG_PUT (DIRECTIONAL), NAKED_CALL→SHORT_CALL, NAKED_PUT→SHORT_PUT (DIRECTIONAL), PCS/CCS (VERTICAL, bullish/bearish), VERT_CALL_DEBIT→BULL_CALL, VERT_PUT_DEBIT→BEAR_PUT (VERTICAL), LONG_STRADDLE/LONG_STRANGLE (VOLATILITY, neutral), SHORT_STRADDLE/SHORT_STRANGLE (NEUTRAL), IC→IRON_CONDOR, CONDOR_*/BUTTERFLY_*/IRON_BUTTERFLY/CALENDAR_* (NEUTRAL), DIAGONAL_CALL (DIRECTIONAL bullish) / DIAGONAL_PUT (bearish), COVERED_CALL/PROTECTIVE_PUT/COLLAR (DIRECTIONAL bullish). **Read each label and bias off the builder** rather than inventing them.
- `structure_meta(code, net_delta)` → `(type, family, label, bias, known)`; unknown or `CUSTOM` → `("CUSTOM", "CUSTOM", "Custom structure", bias_from(net_delta), False)` with a ±0.05 deadband to `neutral`.
- `finder_legs(legs, chain, spot)` → `(legs, None)` or `(None, reason)`: per option leg, `ssn.extract_options(chain, kind, 0, 100000)[expiry]["strikes"][strike]` (strike matched within 1e-6); build with `ssn._leg_from(data, kind, side, expiry)`, then `mark = premium` when it is a finite number > 0, and `qty` set. Share leg → `ssn._stock_leg(price or spot)`. Reduce every qty by the smallest (integer division only when all divide; else keep). The reason names the contract: `"No quote for the 80 put expiring Oct 16 - reload the chain"`.

**Step 4:** PASS. **Step 5:** commit `feat(options_svc): turn Calculator legs into a Strategy Finder row`.

---

### Task 3: One helper for ATM IV + daily move (refactor, no behaviour change)

**Files:**
- Modify: `services/options_svc/compute.py` (inside `swing_scan`, the `dem` / `atm_iv` block)
- Test: `services/options_svc/tests/test_rate_trade.py` (append)

**Step 1: Failing test**

```python
def test_vol_inputs_invert_the_daily_move_and_fall_back_to_current_iv():
    import math
    dem, atm = compute.scan_vol_inputs({"expected_moves": {"daily": {"move_dollars": 2.0}}}, 100.0)
    assert dem == 2.0 and math.isclose(atm, 2.0 * math.sqrt(365) / 100)
    assert compute.scan_vol_inputs({"current_iv": 28.0}, 100.0) == (None, 0.28)
    assert compute.scan_vol_inputs({}, 100.0) == (None, 0.20)
```

**Step 2:** FAIL. **Step 3:** Move the existing lines into `def scan_vol_inputs(iv, spot) -> (dem, atm_iv)` verbatim and call it from `swing_scan`. **Step 4:** run the new test AND `python -m pytest services/options_svc -q -p no:randomly -rf` — the failing set must equal the baseline taken before Task 1. **Step 5:** commit `refactor(options_svc): scan_vol_inputs shared by swing_scan and calc_rate`.

---

### Task 4: `rate_trade.rate()` — score and stamp one row

**Files:**
- Modify: `services/options_svc/rate_trade.py`
- Test: `services/options_svc/tests/test_rate_trade.py` (append)

**Step 1: Failing tests** — monkeypatch `compute.se.fetch_price_history`, `compute.se.calc_technicals`, `rate_trade.run_iv_analysis` (import it into the module), `compute.scan_earnings`:

```python
def _stub(monkeypatch, iv=None):
    monkeypatch.setattr(RT.se, "fetch_price_history", lambda c, s: {"candles": []})
    monkeypatch.setattr(RT.se, "calc_technicals", lambda h: {"trend": "up", "rsi14": 55, "price": 100, "sma20": 98})
    monkeypatch.setattr(RT, "run_iv_analysis", lambda *a, **k: iv or {
        "iv_rank": 42.0, "current_iv": 25.0, "hv_current": 20.0,
        "expected_moves": {"daily": {"move_dollars": 1.3}}})
    monkeypatch.setattr(RT.compute, "scan_earnings", lambda s: ("clear", None))

CC = {"symbol": "XYZ", "api": "XYZ", "price": 100.0, "chain": CHAIN}

def test_rate_returns_a_graded_stamped_row(monkeypatch):
    _stub(monkeypatch)
    out = RT.rate("XYZ", "PCS", PCS, CC, market_state=None)
    row = out["row"]
    assert out["error"] is None
    assert row["type"] == "PCS" and row["grade"] in ("Strong", "Good", "Marginal", "Weak")
    assert isinstance(row["composite_score"], (int, float))
    for k in ("friction_pct", "em_to_expiry", "vol_floor", "earnings_status", "iv_rank", "ledger_risk_basis"):
        assert k in row
    assert row["structure_known"] is True and row["_rated_legs"] == PCS

def test_a_weak_trade_is_still_returned_graded(monkeypatch):
    _stub(monkeypatch)
    far = [dict(PCS[0], premium=0.01), dict(PCS[1], premium=0.60)]   # debit-priced credit spread
    row = RT.rate("XYZ", "PCS", far, CC)["row"]
    assert row is not None and row["grade"] == "Weak"

def test_refusals_are_sentences_with_no_row(monkeypatch):
    _stub(monkeypatch)
    assert RT.rate("XYZ", "PCS", PCS, None)["row"] is None
    wrong = RT.rate("ABC", "PCS", PCS, CC)
    assert wrong["row"] is None and "ABC" in wrong["error"]
    assert RT.rate("XYZ", "PCS", [], CC)["error"]

def test_a_cheap_premium_short_is_flagged_not_dropped(monkeypatch):
    _stub(monkeypatch, iv={"iv_rank": 1.0, "current_iv": 25.0, "hv_current": 20.0,
                           "expected_moves": {"daily": {"move_dollars": 1.3}}})
    row = RT.rate("XYZ", "PCS", PCS, CC)["row"]
    assert row is not None and row["vol_gate_blocks"] is True
```

**Step 2:** FAIL. **Step 3: Implement** `rate(symbol, structure, legs, cc, market_state=None) -> {"row", "error"}`:
1. Refuse with a sentence: no legs; no `cc` / no `chain` (`"Load the chain first"`); `cc["symbol"]` ≠ symbol (`f"The loaded chain is for {cc_sym}, not {symbol} - reload"`); no usable `cc["price"]`.
2. `finder_legs` → refusal passthrough.
3. `net_delta` from `ssn.payoff_metrics(legs, spot, symbol)`; `structure_meta(structure, net_delta)`.
4. `row = ssn._assemble(stype, family, label, bias, legs, symbol, spot, atm_iv)` after:
   `client = compute._proxy.schwab_py_client`; `api = cc.get("api") or symbol`; `hist = se.fetch_price_history(client, api)`; `tech = se.calc_technicals(hist) if hist is not None else {}`; `iv = run_iv_analysis(client, api, price=spot, hist=hist, chain=cc["chain"]) or {}`; `dem, atm_iv = compute.scan_vol_inputs(iv, spot)`; `view = ssc.infer_market_view(tech, iv)`; `em_1sd = (dem or 0) * sqrt(max(row_dte, 1))`.
5. `ssc.score_all([row], view, atm_iv, em_1sd, market_state=market_state, daily_move=dem)`.
6. `row["vol_gate_blocks"] = _vol_gate.signal_blocks(row, floor=…SWING, ceiling=…SWING, iv_rank=iv.get("iv_rank"))`; `row["iv_rank"] = iv.get("iv_rank")`; `row["daily_em"] = dem`; `row["structure_known"]`; `row["id"] = f"calc_rate_{symbol}"`; `row["_rated_legs"] = legs` (the page's legs as sent).
7. Earnings via `compute.scan_earnings(symbol)` (guarded → `("not_listed", None)` + `_degrade.degraded("options.calc_rate_earnings")`), then `compute.stamp_candidate(row, trade_type="SWING", earnings=…, iv_rank_known=row["iv_rank"] is not None)`.
8. Any exception after step 1 → `_degrade.degraded("options.calc_rate", detail=symbol)` and `{"row": None, "error": "The rating could not be computed (<ExcClass>)"}`.

**Step 4:** PASS. **Step 5:** commit `feat(options_svc): rate one hand-built trade with the Finder's scorer and stamps`.

---

### Task 5: The `calc_rate` command

**Files:**
- Modify: `services/options_svc/handlers.py` (cache/event constants beside `CACHE_CALC_IV`; `_REPLAY_GUARDED`; a branch beside `calc_iv`; the `handle_command` docstring)
- Test: `services/options_svc/tests/test_handlers.py` (append)

**Step 1: Failing test**

```python
def test_calc_rate_reads_the_calc_chain_and_publishes_the_answer(monkeypatch):
    bus = Bus(fake=True)
    cc = {"symbol": "SPY", "api": "SPY", "price": 450.0, "chain": {"putExpDateMap": {}}}
    bus.cache_set("cache:options:calc_chain", cc)
    bus.cache_set("cache:sentiment:composite", {"derived": {"trend": {"state": "bullish"}}})
    seen = {}
    def _rate(symbol, structure, legs, chain_payload, market_state=None):
        seen.update(symbol=symbol, structure=structure, legs=legs, cc=chain_payload, state=market_state)
        return {"row": {"grade": "Good"}, "error": None}
    monkeypatch.setattr(handlers.rate_trade, "rate", _rate)
    sub = bus.subscribe("events:options:calc_rating")
    legs = [{"option_type": "put", "side": "short", "strike": 440.0, "expiry": "2026-10-16", "qty": 1, "premium": 2.0}]
    handlers.handle_command(bus, Command(type="calc_rate", args={
        "request_id": "r1", "symbol": "SPY", "structure": "NAKED_PUT", "legs": legs}))
    msg = sub.get_message(timeout=1.0); sub.close()
    env = bus.cache_get("cache:options:calc_rating")
    assert env.payload == {"request_id": "r1", "symbol": "SPY", "legs": legs,
                           "row": {"grade": "Good"}, "error": None}
    assert seen["cc"] == cc and seen["state"] == "bullish" and seen["structure"] == "NAKED_PUT"
    assert msg is not None and msg.get("version") == env.version

def test_calc_rate_is_replay_guarded():
    assert "calc_rate" in handlers._REPLAY_GUARDED
```

**Step 2:** FAIL. **Step 3:** implement; the market state read is the swing handler's exact three-level guarded read — factor it to `_market_state(bus)` and use it in both. Document `calc_rate` in the `handle_command` docstring (a guard test requires every command there). **Step 4:** run `test_handlers.py` plus the docstring guard test (`grep -rn "handle_command.__doc__" services/options_svc/tests`). **Step 5:** commit `feat(options_svc): calc_rate command publishes cache:options:calc_rating`.

---

### Task 6: Cross-tier mirror — every template code is mapped

**Files:**
- Modify: `shared/tests/test_cross_tier_mirrors.py`

**Step 1: Failing-first test** (AST only, no imports of either tier): parse `webgui/pages/options/strategies.py` for the keys of the `STRATEGY_TEMPLATES` dict literal and `services/options_svc/rate_trade.py` for the keys of `CALC_TO_SCORER`; assert equal sets. Verify it fails by temporarily deleting one map entry, then restore.

**Step 2–4:** run `python -m pytest shared/tests/test_cross_tier_mirrors.py -q`. **Step 5:** commit `test(mirrors): the rating map covers every Calculator template`.

---

### Task 7: The button, the dialog, the poll (webgui)

**Files:**
- Modify: `webgui/pages/options/calculator.py`
- Modify: `webgui/pages/options/rate_trade.py` (a `request_matches(payload, request_id)` helper, tested)
- Test: `webgui/tests/test_options_calculator_apply.py` (append), `webgui/tests/test_rate_trade.py` (append)

**Step 1: Failing tests**

```python
# test_rate_trade.py
def test_only_the_open_requests_answer_paints():
    assert R.request_matches({"request_id": "a"}, "a")
    assert not R.request_matches({"request_id": "b"}, "a")
    assert not R.request_matches(None, "a") and not R.request_matches({"request_id": "a"}, None)
```

```python
# test_options_calculator_apply.py
def test_rate_button_waits_for_a_chain(page):
    root, _ = page
    btn = [el for el in _walk(root) if getattr(el, "text", None) == "RATE MY TRADE"][0]
    assert not btn.enabled

def test_rate_sends_calc_rate_with_the_legs_and_their_shape(page, sent_commands):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    _click(root, "RATE MY TRADE")
    cmd = [c for c in sent_commands if c["type"] == "calc_rate"][-1]["args"]
    assert cmd["symbol"] == "SPY" and cmd["structure"] == "PCS" and len(cmd["legs"]) == 2
    assert cmd["request_id"]
    assert "Rating…" in _texts(root)

def test_a_matching_rating_paints_the_verdict_and_a_stale_one_does_not(page, sent_commands):
    root, polls = page
    bus_client.bus().cache_set("cache:options:calc_chain", _chain_payload())
    _drive(root, polls)
    _click(root, "RATE MY TRADE")
    rid = [c for c in sent_commands if c["type"] == "calc_rate"][-1]["args"]["request_id"]
    row = {"symbol": "SPY", "type": "PCS", "grade": "Good", "composite_score": 70.0,
           "legs": [], "structure_known": True}
    bus_client.bus().cache_set("cache:options:calc_rating",
                               {"request_id": "someone-else", "row": dict(row, grade="Weak"), "error": None})
    _drive_rating(root)                     # runs _poll_rating + the dialog's paint with a stubbed ctx
    assert "PASS" not in _texts(root)
    bus_client.bus().cache_set("cache:options:calc_rating", {"request_id": rid, "row": row, "error": None})
    _drive_rating(root)
    assert "Good · 70" in _texts(root)

def test_an_error_answer_says_so(page, sent_commands): ...   # error sentence shown, no verdict word
def test_no_answer_in_time_says_the_service_did_not_answer(page, sent_commands): ...  # call the timeout callback
```

`_drive_rating` monkeypatches `checks_feed.read_context` to return `{}` and calls the `_poll_rating` timer callback inside `root` (running a coroutine to completion as `_drive` does). Add `"_poll_rating"` handling without changing `_POLL_NAMES`' existing three.

**Step 2:** FAIL. **Step 3: Implement** in `calculator.py`:
- Beside EXPECTED MOVE: `rate_btn = ui.button("RATE MY TRADE", …)`, same `CALC_BTN` classes, tooltip. `_sync_legs()` sets `rate_btn.set_enabled(_has_contracts(chain) and leg_editor.legs_ready(legs))`.
- Build the dialog ONCE at render time, from the page's outermost column (never cleared): `rating_dialog = ui.dialog()`; inside, a `ui.card().classes("calc-v3 …")` with a banner column (`rate-banner`), a status label (`rate-status`), and `rating_panel = detail.render(width=420)`.
- `rate_trade_click()`: if `state["rating_id"]` pending → return. `rid = uuid4().hex`; `state.update(rating_id=rid, rating_ver=bus_client.read_version("options:calc_rating"))`; status "Rating…", banner cleared, `rating_panel.clear()`; `bus_client.request("options", {"type": "calc_rate", "args": {"request_id": rid, "symbol": _sym(), "structure": sim_view.template_for(editor.get_legs()) or "CUSTOM", "legs": editor.get_legs()}})`; `rating_dialog.open()`; `ui.timer(_overlay.LOAD_TIMEOUT_SEC, lambda r=rid: _rating_timeout(r), once=True)`.
- `@guard_async async def _poll_rating()`: version gate like `_poll_result`; payload = `bus_client.read(...)`; `if not rate_trade.request_matches(payload, state.get("rating_id")): return`; `state["rating_id"] = None`; error → status sentence; else `ctx = await run.io_bound(checks_feed.read_context)`; `cand = detail.checklist_candidate(row, True)`; `items = checks_feed.checks_for(cand, ctx)`; `summ = checks.summary(items)`; `view = rate_trade.banner_view(row, summ["state"], items)`; paint banner (word with `view["tone"]`, `f"{grade} · {score}"`, reasons as `text-xs` lines, and one muted line: "Graded with the Strategy Finder's scorer and checklist; not fitted to outcomes."); `rating_panel.update(strategy_table.detail_signal(row), candidate=cand, ctx=ctx)`. `ui.timer(1.0, _poll_rating)`.
- `_rating_timeout(rid)`: if `state["rating_id"] == rid` → clear it; status "The rating service did not answer - try again."
- Leg edits while the dialog is open do not re-rate; the banner's header names the legs it rated (`detail.contract_lines(row)` already shows them in the panel).

**Step 4:** run `test_rate_trade.py`, `test_options_calculator_apply.py`, `test_options_calculator.py`, `test_no_inline_style.py`. **Step 5:** commit `feat(calc): RATE MY TRADE button and verdict dialog`.

---

### Task 8: Docs

**Files:** `webgui/page_help.py` (Calculator guide: the button, the three words, what feeds them, the not-fitted caveat), `docs/manuals/user-guide/user-guide.md`, `docs/manuals/reference-guide/reference-guide.md` (Calculator section), `docs/manuals/api-reference/api-reference.md` (`calc_rate` args + `cache:options:calc_rating` shape), `docs/webgui-routes.md` (Calculator paragraph), `docs/CHANGELOG.md` (entry), `CLAUDE.md` (route-table row one clause only). Rebuild the three manuals with `docs/manuals/build_docs.py <name>`.

Run `webgui/tests/test_page_help.py`, `test_docs_cover_the_ui.py`, `shared/tests/test_cross_tier_mirrors.py`. Commit `docs: Rate my trade`.

---

### Task 9: Verify

1. Full suites, failing SET compared to the pre-Task-1 baseline: `webgui` (`cd webgui && python -m pytest -q -p no:randomly -rf`), `services/options_svc`, `shared/tests`, `options-scanner/tests -p no:randomly`.
2. Local harness (scratchpad `harness.py`, temporary `calc-harness` entry in `.claude/launch.json`, reverted after): stub `rate_trade`'s history/IV/earnings like the tests; load SPY, click RATE MY TRADE, confirm the dialog paints a verdict, grade and the panel at 1280 px; check a Weak structure reads PASS; check the timeout line by not answering. Disable transitions before reading any computed style.
3. Hand over the push + PowerShell promote script only when asked.
