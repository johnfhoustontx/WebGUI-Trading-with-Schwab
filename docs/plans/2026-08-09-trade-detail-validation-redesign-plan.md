# Trade Detail Panel — Validation + Triage Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix seven validated correctness defects in the shared Trade detail panel and restructure it from a verification layout into a triage layout with dealbreaker flags above the fold.

**Architecture:** `webgui/pages/options/detail.py` is a Tier-1 NiceGUI page helper — it imports only `nicegui`, sibling page modules, and `theme.py`. It never imports an engine. All new logic goes into **pure module-level functions** that take plain dicts and return plain data, so they are unit-testable without a NiceGUI context. `render()` stays thin: widgets plus wiring. The four adapters (`strategy_table.detail_signal`, `paper.synth_from_trade`, `captured.synth_from_captured`, and the Scanner's raw signal) are normalized so the panel receives one consistent unit convention.

**Tech Stack:** Python 3.11, NiceGUI, pytest, Tailwind utility classes via `theme.py` design tokens.

**Design doc:** `docs/plans/2026-08-09-trade-detail-validation-redesign-design.md`

---

## Before you start

**Read the design doc first.** It records *why* each defect matters; this plan only says how to fix it.

**Run the test suite and record the baseline:**

```bash
cd webgui && ../.venv/Scripts/python -m pytest -q
```

Expected: **1053 passed**. If your number differs, that is your baseline — write it down and compare the *set* of failures, not the count. This repo has documented pre-existing failures elsewhere; `webgui` should be fully green.

**Key project rules that bind this work:**

- **Tailwind-first is mandatory.** No `.style()`, no inline `style=` strings, no `.props("style=…")`. Use `.classes()` with tokens from `theme.py`. `webgui/tests/test_no_inline_style.py` enforces this.
- **Data-driven colors map to a finite palette.** Never build a color class from a runtime value. Map a semantic state to one of `TXT_POS` / `TXT_WARN` / `TXT_NEG` / `TXT_NEUTRAL`.
- **Reactive recolors must use `remove=`.** `.classes()` accumulates. Swapping a color means `.classes(remove=STATE_TEXT_CLASSES, add=NEW)` or repeated repaints stack conflicting classes. `_set_color` in `detail.py` already does this — use it.
- **This repo has concurrent sessions editing it.** Never run a bare `git add .` or `git add -A`. Always `git add <explicit paths>` and check `git status --short` before committing.

**Available theme tokens** (from `webgui/pages/options/theme.py`): `CARD`, `TILE_3D`, `EYEBROW`, `LABEL`, `MUTED`, `BTN`, `BTN_PRIMARY`, `TXT_POS`, `TXT_WARN`, `TXT_NEG`, `TXT_NEUTRAL`, `STATE_TEXT_CLASSES`.

---

## Task 1: Unit normalization — the highest-severity defect

Scanner and Swing emit **per-share** dollars. The paper adapter mixes per-share `entry_credit` with whole-position `max_loss_total` **in the same row**. The panel must display one consistent unit.

**Decision:** every adapter emits **per-share** dollars, and the panel multiplies by 100 for its per-contract display. Per-share is the common denominator all four sources can produce, and it keeps the conversion in exactly one place.

**Files:**
- Modify: `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_options_detail.py`

**Step 1: Write the failing tests**

Add to `webgui/tests/test_options_detail.py`:

```python
def test_per_contract_multiplies_per_share_by_100():
    assert detail.per_contract(1.55) == 155.0
    assert detail.per_contract(3.45) == 345.0


def test_per_contract_passes_through_none_and_junk():
    assert detail.per_contract(None) is None
    assert detail.per_contract("n/a") is None


def test_money_per_contract_labels_its_unit():
    assert detail.money_per_contract(1.55) == "$155.00 per contract"
    assert detail.money_per_contract(None) == "—"
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k per_contract
```

Expected: FAIL — `AttributeError: module 'pages.options.detail' has no attribute 'per_contract'`.

**Step 3: Implement**

Add near the other formatters in `detail.py`:

```python
# Every adapter emits PER-SHARE dollars; the panel displays per-contract. One
# option contract covers 100 shares. Keeping the conversion in exactly one place
# is the fix for the unit collision documented in the design doc -- the paper
# adapter previously mixed per-share credit with whole-position max loss in the
# same row.
CONTRACT_MULTIPLIER = 100


def per_contract(per_share):
    """Per-share dollars -> per-contract dollars. None for anything non-numeric."""
    if isinstance(per_share, bool) or not isinstance(per_share, (int, float)):
        return None
    return float(per_share) * CONTRACT_MULTIPLIER


def money_per_contract(per_share):
    """Formatted per-contract dollars carrying an explicit unit, or an em-dash."""
    v = per_contract(per_share)
    return "—" if v is None else f"${v:,.2f} per contract"
```

> `isinstance(x, bool)` is excluded deliberately — `True` is an `int` in Python and would render as `$100.00`.

**Step 4: Run to verify they pass**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k per_contract
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "feat(detail): per-contract money formatter with explicit unit"
```

---

## Task 2: Fix the paper adapter's within-row unit mismatch

**Files:**
- Modify: `webgui/pages/options/paper.py:237-238`
- Test: `webgui/tests/test_options_paper.py`

**Step 1: Write the failing test**

```python
def test_synth_max_loss_is_per_share_not_whole_position():
    # max_loss_total is max_loss * quantity * 100 (paper_trader.py:117).
    # With qty=3 and a $3.45/share max loss, the stored total is $1035.
    trade = {"symbol": "SPY", "entry_credit": 1.55, "max_loss_total": 1035.0,
             "quantity": 3, "expiration": "2099-01-15"}
    s = paper.synth_from_trade(trade)
    # credit is per-share, so max_loss must be per-share too.
    assert s["max_loss"] == pytest.approx(3.45)
    assert s["credit"] == pytest.approx(1.55)


def test_synth_max_loss_none_when_quantity_missing():
    # Without quantity the total cannot be reduced to per-share. Better to show
    # nothing than a figure that is wrong by a factor of the position size.
    trade = {"symbol": "SPY", "entry_credit": 1.55, "max_loss_total": 1035.0,
             "expiration": "2099-01-15"}
    assert paper.synth_from_trade(trade)["max_loss"] is None
```

Ensure `import pytest` is present at the top of the file.

**Step 2: Run to verify it fails**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_paper.py -q -k per_share
```

Expected: FAIL — `max_loss` is `1035.0`.

**Step 3: Implement**

In `paper.py`, add a helper above `synth_from_trade`:

```python
def _max_loss_per_share(t):
    """Whole-position ``max_loss_total`` reduced to per-share dollars.

    ``paper_trader.py:117`` stores ``max_loss_total = max_loss * quantity * 100``
    while ``entry_credit`` is PER SHARE, so feeding both to the detail panel
    displayed a per-share credit beside a whole-position max loss -- a mismatch
    that scales with quantity. Prefer a stored per-share ``max_loss``; otherwise
    divide the total back down. Returns None when quantity is unknown, since a
    total cannot be reduced without it and a wrong number is worse than none.
    """
    direct = _num(t.get("max_loss"))
    if direct is not None:
        return direct
    total = _num(t.get("max_loss_total"))
    qty = _num(t.get("quantity"))
    if total is None or not qty:
        return None
    return round(total / (qty * 100.0), 4)
```

Then change the `max_loss` line in `synth_from_trade` from:

```python
        "max_loss": _num(t.get("max_loss_total")),
```

to:

```python
        "max_loss": _max_loss_per_share(t),
```

**Step 4: Run the full paper suite**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_paper.py -q
```

Expected: PASS. If an existing test asserted the old whole-position value, update it — the old value was the bug.

**Step 5: Commit**

```bash
git add webgui/pages/options/paper.py webgui/tests/test_options_paper.py
git status --short
git commit -m "fix(detail): paper adapter mixed per-share credit with whole-position max loss"
```

---

## Task 3: Iron condor breakeven currently never renders

The engine stores it as the string `"5900.5/6010.2"` (`scanner_engine.py:1069`); `_money` requires a number, so every IC shows an em-dash.

**Files:**
- Modify: `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_options_detail.py`

**Step 1: Write the failing tests**

```python
def test_breakevens_parses_iron_condor_string():
    assert detail.breakevens("5900.5/6010.2") == [5900.5, 6010.2]


def test_breakevens_accepts_a_plain_number():
    assert detail.breakevens(398.45) == [398.45]


def test_breakevens_returns_empty_for_missing_or_junk():
    assert detail.breakevens(None) == []
    assert detail.breakevens("") == []
    assert detail.breakevens("not/a/number") == []


def test_breakeven_text_formats_both_sides():
    assert detail.breakeven_text("5900.5/6010.2") == "$5,900.50 / $6,010.20"
    assert detail.breakeven_text(398.45) == "$398.45"
    assert detail.breakeven_text(None) == "—"
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k breakeven
```

Expected: FAIL — no attribute `breakevens`.

**Step 3: Implement**

```python
def breakevens(raw):
    """Normalize a breakeven field to a list of floats.

    Iron condors store TWO breakevens as the string "put_be/call_be"
    (scanner_engine.py:1069). The old code formatted with ``_money``, which
    requires a number, so every IC rendered an em-dash. Credit spreads store a
    plain float. Both shapes are handled; anything unparseable yields [].
    """
    if raw is None:
        return []
    if isinstance(raw, bool):
        return []
    if isinstance(raw, (int, float)):
        return [float(raw)]
    out = []
    for part in str(raw).split("/"):
        try:
            out.append(float(part.strip()))
        except (TypeError, ValueError):
            return []
    return out


def breakeven_text(raw):
    """Display string for one or two breakevens, or an em-dash when absent."""
    vals = breakevens(raw)
    if not vals:
        return "—"
    return " / ".join(f"${v:,.2f}" for v in vals)
```

**Step 4: Run to verify they pass**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k breakeven
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "fix(detail): iron condor breakeven string never rendered"
```

---

## Task 4: Factor provenance — missing must not render as a score

`scoring.py` collapses "unavailable" into `0.0` for `rr`/`pop`/`theta` and `50.0` for the other eight. The panel draws both as confident bars.

**Files:**
- Modify: `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_options_detail.py`

**Step 1: Write the failing tests**

```python
def test_factor_rows_marks_absent_factor_as_unknown():
    rows = detail.factor_rows({"rr": 80}, "PCS")
    by_label = {label: (val, known) for label, val, known in rows}
    assert by_label["R:R"] == (80, True)
    assert by_label["PoP"][1] is False       # absent -> unknown, NOT 0


def test_factor_rows_respects_explicit_unavailable_list():
    # Tier 2 may emit factors_unavailable; a present-but-sentinel value is then
    # known to be missing rather than merely suspected.
    rows = detail.factor_rows({"liq": 50}, "PCS", unavailable=["liq"])
    by_label = {label: known for label, _val, known in rows}
    assert by_label["Liquidity"] is False


def test_factor_rows_keeps_a_genuine_zero_known():
    rows = detail.factor_rows({"liq": 0}, "PCS")
    by_label = {label: (val, known) for label, val, known in rows}
    assert by_label["Liquidity"] == (0, True)   # a real wide-spread reading


def test_factor_value_text_shows_dash_for_unknown():
    assert detail.factor_value_text(80, True) == "80"
    assert detail.factor_value_text(None, False) == "—"
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k factor
```

Expected: FAIL — `factor_rows` returns 2-tuples.

**Step 3: Implement**

Replace `factor_rows` in `detail.py`:

```python
def factor_rows(factor_scores, trade_type, unavailable=None):
    """[(label, value, known), ...] for the Score factors card.

    ``known`` is False when the factor was never measured. This matters because
    scoring.py collapses "unavailable" into a real-looking number in BOTH
    directions -- 0.0 for rr/pop/theta and 50.0 for the other eight -- and the
    panel previously drew both as confident bars. A calm mid-grey 50 could mean
    "never measured", which for triage is false reassurance.

    Absence from the dict is proof. ``unavailable`` is the additive
    ``factors_unavailable`` list from Tier 2 when present -- exact rather than
    inferred. A value that IS present and is not listed is treated as real,
    including a genuine 0 (a real wide-spread liquidity reading).
    """
    fs = factor_scores or {}
    missing = set(unavailable or ())
    if trade_type == "IC":
        keys = [("pcs_leg", "Put leg"), ("ccs_leg", "Call leg"),
                ("delta_bonus", "Delta bonus")]
    else:
        keys = FACTOR_LABELS
    rows = []
    for key, label in keys:
        raw = fs.get(key)
        known = raw is not None and key not in missing
        rows.append((label, raw if known else None, known))
    return rows


def factor_value_text(value, known):
    """Numeric text for a factor bar, or an em-dash when it was never measured."""
    if not known or not isinstance(value, (int, float)):
        return "—"
    return f"{value:g}"
```

**Step 4: Update the existing callers and tests**

`_build_cards` unpacks 2-tuples — update the loop (Task 10 rebuilds this card fully; for now just keep it running):

```python
        for label, val, known in factor_rows(s.get("factor_scores"), s.get("type"),
                                             s.get("factors_unavailable")):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(label).classes("text-xs w-20 opacity-80")
                ui.html(svg.gradient_bar_svg(val if known else 0))
                ui.label(factor_value_text(val, known)).classes("text-xs w-8 text-right")
```

Three existing tests assert the old shape (`test_factor_rows_returns_11_for_non_ic`, `test_factor_rows_ic_variant`, `test_factor_rows_missing_values_default_zero`). Update the first two to unpack 3-tuples. **Replace the third entirely** — it asserted the exact behavior we are removing:

```python
def test_factor_rows_missing_values_are_unknown_not_zero():
    rows = detail.factor_rows({}, "PCS")
    assert all(known is False and val is None for _label, val, known in rows)
```

**Step 5: Run the full detail suite**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q
```

Expected: PASS.

**Step 6: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "fix(detail): distinguish unmeasured factors from scored zeros"
```

---

## Task 5: The flag engine

**Files:**
- Modify: `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_options_detail.py`

**Step 1: Write the failing tests**

```python
def test_flag_inside_expected_move():
    flags = detail.flags_for({"factor_scores": {"em": 30}})
    assert any(f["key"] == "em" and f["state"] == "tripped" for f in flags)


def test_no_em_flag_when_outside_the_move():
    flags = detail.flags_for({"factor_scores": {"em": 70}})
    assert not any(f["key"] == "em" for f in flags)


def test_flag_thin_liquidity_only_when_measured():
    sig = {"factor_scores": {"liq": 20}, "bid": 1.10, "ask": 1.20}
    assert any(f["key"] == "liq" and f["state"] == "tripped"
               for f in detail.flags_for(sig))


def test_liquidity_unmeasured_when_bid_ask_absent():
    flags = detail.flags_for({"factor_scores": {"liq": 50}})
    liq = [f for f in flags if f["key"] == "liq"]
    assert liq and liq[0]["state"] == "unmeasured"


def test_flag_thin_credit_uses_rr_pct():
    assert any(f["key"] == "rr" for f in detail.flags_for({"rr_pct": 12}))
    assert not any(f["key"] == "rr" for f in detail.flags_for({"rr_pct": 35}))


def test_flag_near_gamma_wall():
    assert any(f["key"] == "gex" for f in detail.flags_for({"factor_scores": {"gex": 10}}))


def test_clean_signal_has_no_flags():
    sig = {"rr_pct": 40, "bid": 1.10, "ask": 1.12,
           "factor_scores": {"em": 80, "liq": 95, "trend": 90, "gex": 90, "dex": 90}}
    assert detail.flags_for(sig) == []


def test_flags_never_raise_on_garbage():
    for bad in (None, {}, {"factor_scores": None}, {"factor_scores": {"em": "x"}},
                {"rr_pct": "n/a"}):
        assert isinstance(detail.flags_for(bad), list)


def test_flag_count_counts_only_tripped_and_unmeasured():
    assert detail.flag_count({"factor_scores": {"em": 30, "gex": 10}}) >= 2
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k flag
```

Expected: FAIL — no attribute `flags_for`.

**Step 3: Implement**

```python
#############################################
# DEALBREAKER FLAGS
#############################################
# Triage is mostly fast rejection, so the four dealbreakers get their own
# treatment above the fold rather than being four of eleven identical bars.
#
# Three bars fall out of scoring.py's own definitions and invent nothing:
#   em    < 50  -- norm_em_buffer returns 0-50 ONLY inside 1 sigma (scoring.py:208)
#   trend < 50  -- 25 = partially against, 0 = against    (scoring.py:248)
#   liq   < 50  -- 50 => spread > 3% of mark, zero at 5%  (scoring.py:231)
#
# The two below are judgment calls. Tune in use; promote to Settings only if
# they turn out to change often.
MIN_RR_PCT = 20        # credit too thin: norm_rr reaches 100 at 50%
WALL_FLAG_BAR = 30     # too close to a gamma wall: 100 = >=1% of spot away

_SEMANTIC_BAR = 50     # the scorer's own neutral/boundary point


def _score(fs, key):
    v = fs.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def flags_for(signal):
    """Dealbreaker flags for a signal: [{key, label, state}, ...].

    ``state`` is "tripped" (measured and beyond the bar) or "unmeasured" (the
    inputs were never available). An unmeasured dealbreaker is surfaced rather
    than hidden -- scoring.py renders missing data as a neutral-looking 50, so
    silence would be false reassurance.

    Total over adversarial input: never raises, always returns a list.
    """
    s = signal or {}
    if not isinstance(s, dict):
        return []
    fs = s.get("factor_scores") or {}
    if not isinstance(fs, dict):
        fs = {}
    unavailable = set(s.get("factors_unavailable") or ())
    out = []

    def add(key, label, tripped, measured):
        if not measured:
            out.append({"key": key, "label": f"{label} not measured",
                        "state": "unmeasured"})
        elif tripped:
            out.append({"key": key, "label": label, "state": "tripped"})

    # Liquidity -- bid/ask presence on the signal is direct proof of measurement.
    liq = _score(fs, "liq")
    liq_measured = (s.get("bid") is not None and s.get("ask") is not None
                    and "liq" not in unavailable)
    if liq is not None or not liq_measured:
        add("liq", "Thin liquidity", liq is not None and liq < _SEMANTIC_BAR,
            liq is not None and liq_measured)

    # Credit vs risk -- rr_pct rides on the signal, so presence is proof.
    rr = s.get("rr_pct")
    rr_val = float(rr) if isinstance(rr, (int, float)) and not isinstance(rr, bool) else None
    if rr_val is not None:
        add("rr", "Credit thin for the risk", rr_val < MIN_RR_PCT, True)

    # Expected move / trend / walls -- raw inputs are not on the signal, so an
    # exact 50.0 is the only page-side hint of "never measured". Suggestive, not
    # proof; factors_unavailable from Tier 2 makes it exact when present.
    for key, label, bar in (("em", "Short strike inside 1σ move", _SEMANTIC_BAR),
                            ("trend", "Trend against the structure", _SEMANTIC_BAR),
                            ("gex", "Near a gamma wall", WALL_FLAG_BAR),
                            ("dex", "Near a delta wall", WALL_FLAG_BAR)):
        val = _score(fs, key)
        if val is None:
            continue
        measured = key not in unavailable and val != float(_SEMANTIC_BAR)
        add(key, label, val < bar, measured)
    return out


def flag_count(signal):
    """How many flags a signal raises -- drives the collapsed-strip badge."""
    return len(flags_for(signal))


def flag_class(state):
    """Finite state -> a fixed palette class (never build a class from a value)."""
    return TXT_NEG if state == "tripped" else TXT_WARN
```

**Step 4: Run to verify they pass**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k flag
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "feat(detail): dealbreaker flag engine with measured/unmeasured provenance"
```

---

## Task 6: Contract lines — strikes as an instruction

`_strikes_text` renders `$400 - $395 (5-wide)`, which reads as a descending range and hides which leg is short.

**Files:**
- Modify: `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_options_detail.py`

**Step 1: Write the failing tests**

```python
def test_contract_lines_put_credit_spread():
    sig = {"type": "PCS", "short_strike": 400, "long_strike": 395, "width": 5}
    assert detail.contract_lines(sig) == ["Sell 400 P  /  Buy 395 P", "5 wide"]


def test_contract_lines_call_credit_spread():
    sig = {"type": "CCS", "short_strike": 420, "long_strike": 425, "width": 5}
    assert detail.contract_lines(sig)[0] == "Sell 420 C  /  Buy 425 C"


def test_contract_lines_iron_condor_has_two_legs():
    sig = {"type": "IC", "short_strike": 390, "long_strike": 385,
           "call_short": 420, "call_long": 425}
    lines = detail.contract_lines(sig)
    assert "Sell 390 P  /  Buy 385 P" in lines
    assert "Sell 420 C  /  Buy 425 C" in lines


def test_contract_lines_empty_when_no_strikes():
    assert detail.contract_lines({"type": "PCS"}) == []
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k contract_lines
```

Expected: FAIL.

**Step 3: Implement**

```python
def _leg_pair(short_k, long_k, right):
    """One 'Sell X / Buy Y' instruction line, or None when strikes are absent."""
    if short_k is None or long_k is None:
        return None
    return f"Sell {short_k:g} {right}  /  Buy {long_k:g} {right}"


def contract_lines(signal):
    """The position as instructions rather than a range.

    '$400 - $395 (5-wide)' read as a descending range and hid which leg was
    short. An iron condor yields two lines, one per vertical.
    """
    s = signal or {}
    lines = []
    if s.get("type") == "IC":
        for sk, lk, right in ((s.get("short_strike"), s.get("long_strike"), "P"),
                              (s.get("call_short"), s.get("call_long"), "C")):
            line = _leg_pair(sk, lk, right)
            if line:
                lines.append(line)
        return lines
    right = "C" if str(s.get("type", "")).upper().startswith("CC") else "P"
    line = _leg_pair(s.get("short_strike"), s.get("long_strike"), right)
    if line:
        lines.append(line)
    w = s.get("width")
    if lines and isinstance(w, (int, float)) and not isinstance(w, bool):
        lines.append(f"{w:g} wide")
    return lines
```

> `:g` drops trailing zeros so `400.0` prints as `400`.

**Step 4: Run to verify they pass**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k contract_lines
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "feat(detail): render strikes as sell/buy instructions"
```

---

## Task 7: The gauge must always name its metric

It shows composite score, or silently falls back to PoP for paper trades, while still captioned with `grade`.

**Files:**
- Modify: `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_options_detail.py`

**Step 1: Write the failing tests**

```python
def test_gauge_metric_prefers_composite_score():
    m = detail.gauge_metric({"composite_score": 72, "grade": "Good"})
    assert m == {"value": 72, "caption": "Composite score", "grade": "Good"}


def test_gauge_metric_falls_back_to_pop_and_relabels():
    # A paper trade never stored a composite. Showing PoP under the composite's
    # caption and grade would put two different 0-100 scales on one face.
    m = detail.gauge_metric({"pop_pct": 68})
    assert m["value"] == 68
    assert m["caption"] == "Probability of profit"
    assert m["grade"] == ""


def test_gauge_metric_when_nothing_is_available():
    m = detail.gauge_metric({})
    assert m["value"] == 0
    assert m["caption"] == "No score available"
    assert m["grade"] == ""
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k gauge_metric
```

Expected: FAIL.

**Step 3: Implement**

```python
def gauge_metric(signal):
    """What the gauge shows, always with the caption naming it.

    The old code fell back from composite_score to pop_pct while keeping the
    composite's grade caption -- two different 0-100 scales on one unlabelled
    face. The grade belongs to the composite ONLY, so the PoP fallback carries
    no grade.
    """
    s = signal or {}
    score = s.get("composite_score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return {"value": score, "caption": "Composite score",
                "grade": s.get("grade") or ""}
    pop = s.get("pop_pct")
    if isinstance(pop, (int, float)) and not isinstance(pop, bool):
        return {"value": pop, "caption": "Probability of profit", "grade": ""}
    return {"value": 0, "caption": "No score available", "grade": ""}
```

**Step 4: Run to verify they pass**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k gauge_metric
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "fix(detail): gauge silently switched between composite and PoP scales"
```

---

## Task 8: Stop fabricating an IV reading, and disambiguate DTE

**Files:**
- Modify: `webgui/pages/options/detail.py`, `webgui/pages/options/captured.py`
- Test: `webgui/tests/test_options_detail.py`, `webgui/tests/test_options_captured.py`

**Step 1: Write the failing tests**

In `test_options_detail.py`:

```python
def test_iv_marker_suppressed_without_current_iv():
    # Falling back to the 52w LOW drew the marker at the bottom of the range,
    # reading as "IV is dirt cheap" when the truth was "unknown".
    assert detail.iv_marker_value({"iv_low_52w": 10, "iv_high_52w": 40}) is None


def test_iv_marker_uses_current_iv_when_present():
    assert detail.iv_marker_value(
        {"iv_low_52w": 10, "iv_high_52w": 40, "current_iv": 22}) == 22


def test_dte_text_distinguishes_live_from_entry():
    assert detail.dte_text({"dte": 12}) == "12 DTE"
    assert detail.dte_text({"dte": 12, "dte_is_entry": True}) == "12 DTE at entry"
    assert detail.dte_text({}) == "—"
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k "iv_marker or dte_text"
```

Expected: FAIL.

**Step 3: Implement**

```python
def iv_marker_value(signal):
    """Current IV for the 52-week range marker, or None to draw no marker.

    The old default of ``current_iv or iv_low_52w`` planted the marker at the
    52-week LOW whenever current IV was missing, which reads as a confident
    "IV is cheap" rather than "unknown".
    """
    s = signal or {}
    v = s.get("current_iv")
    if v is None:
        v = s.get("short_iv")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    return None


def dte_text(signal):
    """Days to expiry, saying so when the figure is days-at-ENTRY.

    Captured signals store ``dte_at_entry``; an aged signal was displaying a DTE
    that had already elapsed under the same label as a live one.
    """
    s = signal or {}
    dte = s.get("dte")
    if not isinstance(dte, (int, float)) or isinstance(dte, bool):
        return "—"
    return f"{dte:g} DTE at entry" if s.get("dte_is_entry") else f"{dte:g} DTE"
```

Then in `_build_cards`, guard the IV marker:

```python
            marker = iv_marker_value(s)
            if (marker is not None
                    and isinstance(s.get("iv_low_52w"), (int, float))
                    and isinstance(s.get("iv_high_52w"), (int, float))):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("52w").classes("text-xs w-12 opacity-80")
                    ui.html(svg.range_marker_svg(s["iv_low_52w"], s["iv_high_52w"], marker))
```

In `captured.py` `synth_from_captured`, add the flag beside `dte`:

```python
        "dte": r.get("dte_at_entry"),
        "dte_is_entry": r.get("dte_at_entry") is not None,
```

Add a captured test:

```python
def test_synth_marks_dte_as_entry_value():
    s = captured.synth_from_captured({"symbol": "SPY", "dte_at_entry": 12})
    assert s["dte_is_entry"] is True
```

**Step 4: Run both suites**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py tests/test_options_captured.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/pages/options/captured.py webgui/tests/test_options_detail.py webgui/tests/test_options_captured.py
git status --short
git commit -m "fix(detail): fabricated IV marker and ambiguous DTE label"
```

---

## Task 9: Debit structures must show their cost

`detail_signal` leaves `credit` unset for debits so it is not mislabelled — correct, but nothing then renders the cost.

**Files:**
- Modify: `webgui/pages/options/strategy_table.py:273-288`, `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_strategy_table.py`, `webgui/tests/test_options_detail.py`

**Step 1: Write the failing tests**

In `test_strategy_table.py`:

```python
def test_detail_signal_carries_debit_as_negative_net_cost():
    out = strategy_table.detail_signal({"net_debit": 2.40})
    assert out["credit"] is None          # still not mislabelled as a credit
    assert out["net_cost"] == -2.40


def test_detail_signal_credit_is_positive_net_cost():
    out = strategy_table.detail_signal({"net_credit": 1.55})
    assert out["net_cost"] == 1.55
```

In `test_options_detail.py`:

```python
def test_cost_row_labels_credit_and_debit():
    assert detail.cost_row({"net_cost": 1.55}) == ("Credit", "$155.00 per contract")
    assert detail.cost_row({"net_cost": -2.40}) == ("Debit", "$240.00 per contract")
    assert detail.cost_row({}) == ("Credit", "—")
```

**Step 2: Run to verify they fail**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_strategy_table.py tests/test_options_detail.py -q -k "net_cost or cost_row"
```

Expected: FAIL.

**Step 3: Implement**

In `strategy_table.detail_signal`, before the `return`:

```python
    # Signed cost: positive = credit received, negative = debit paid. ``credit``
    # deliberately stays None for a debit so the green Credit tile cannot show a
    # DEBIT mislabelled as a credit -- but without this the cost vanished
    # entirely, so a long call showed no price at all.
    if out.get("net_cost") is None:
        if out.get("net_credit") is not None:
            out["net_cost"] = out["net_credit"]
        elif out.get("net_debit") is not None:
            out["net_cost"] = -abs(out["net_debit"])
        elif out.get("credit") is not None:
            out["net_cost"] = out["credit"]
```

In `detail.py`:

```python
def cost_row(signal):
    """(label, text) for the money-in/out row -- 'Credit' or 'Debit'.

    Magnitude is always shown positive; the LABEL carries the direction.
    """
    s = signal or {}
    v = s.get("net_cost")
    if v is None:
        v = s.get("credit")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return ("Credit", "—")
    label = "Debit" if v < 0 else "Credit"
    return (label, money_per_contract(abs(v)))
```

**Step 4: Run to verify they pass**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_strategy_table.py tests/test_options_detail.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/strategy_table.py webgui/pages/options/detail.py webgui/tests/test_strategy_table.py webgui/tests/test_options_detail.py
git status --short
git commit -m "feat(detail): show debit cost instead of hiding it"
```

---

## Task 10: Pin the paper vega round-trip

Not a bug — `paper_trader.py:123` stores `entry_vega = -net_vega` and `paper.py:249` inverts it back. Pin it so a future edit to either side cannot silently break it.

**Files:**
- Test only: `webgui/tests/test_options_paper.py`

**Step 1: Write the test**

```python
def test_vega_sign_round_trips_through_storage():
    # paper_trader.py:123 stores entry_vega = -signal["net_vega"], so the
    # adapter's -entry_vega recovers the ORIGINAL net_vega exactly. This test
    # exists to fail loudly if either side flips independently.
    original_net_vega = -0.20
    stored_entry_vega = -original_net_vega          # what paper_trader writes
    s = paper.synth_from_trade({"symbol": "SPY", "entry_vega": stored_entry_vega,
                                "expiration": "2099-01-15"})
    assert s["net_vega"] == pytest.approx(original_net_vega)
```

**Step 2: Run — it should pass immediately**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_paper.py -q -k vega
```

Expected: PASS. This is a characterization test, not a red-green cycle.

**Step 3: Commit**

```bash
git add webgui/tests/test_options_paper.py
git status --short
git commit -m "test(detail): pin paper vega sign round-trip"
```

---

## Task 11: Rebuild the panel layout

Every pure helper now exists. This task is presentation only.

**Files:**
- Modify: `webgui/pages/options/detail.py` — `_build_cards`, `_Handle.update`, `render`
- Test: `webgui/tests/test_options_detail.py`, `webgui/tests/test_no_inline_style.py`

**Step 1: Restructure `render()`**

Inside the existing `col`, after the title row, replace the header block:

```python
        header = ui.column().classes("w-full gap-1")
        with header:
            sig_title = ui.label("").classes("text-subtitle1 font-bold")
            sig_sub = ui.label("").classes(f"text-xs {MUTED}")
            gauge_el = ui.highchart(gauge_figure(0, "", height=104)) \
                .classes("self-center w-[160px] h-[104px]")
            gauge_caption = ui.label("").classes(f"text-xs self-center {EYEBROW}")
            flag_box = ui.column().classes("w-full gap-1")
        header.set_visibility(False)
        body = ui.column().classes("w-full gap-2")
```

The gauge stays a persistent element created at page build — a `ui.highchart` added only on selection fails with `Failed to resolve module specifier nicegui-highcharts`. Do not move it.

Drop the 2×2 `_TILES` grid and `_tile_slot`; economics move into `_build_cards`.

**Step 2: Rewrite `_Handle.update`**

```python
    def update(self, signal):
        if not signal:
            self.clear()
            return
        s = signal
        self._state["has_signal"] = True
        self._header.set_visibility(self._state["open"])
        self._sig_title.text = _signal_title(s)
        self._sig_sub.text = " · ".join(
            x for x in (s.get("trade_type", ""), dte_text(s)) if x and x != "—")

        m = gauge_metric(s)
        self._gauge.options = gauge_figure(m["value"] or 0, m["grade"], height=104)
        self._gauge.update()
        self._caption.text = m["caption"]

        flags = flags_for(s)
        self._flag_box.clear()
        with self._flag_box:
            for f in flags:
                ui.label(f"⚠ {f['label']}").classes(
                    f"text-xs {flag_class(f['state'])}")
        self._set_flag_badge(len(flags))

        self._body.clear()
        with self._body:
            _build_cards(s)
```

`clear()` must also reset the badge to 0.

**Step 3: Rewrite `_build_cards` in ladder order**

```python
def _build_cards(s):
    """Contract, then economics, then collapsed detail — reject/verify/explore."""
    lines = contract_lines(s)
    if lines:
        with ui.column().classes(f"w-full gap-0 {CARD}"):
            for line in lines:
                ui.label(line).classes("text-sm font-bold")
            exp = s.get("expiration")
            if exp:
                ui.label(f"Exp {exp}").classes(f"text-xs {MUTED}")

    with ui.column().classes("w-full gap-1"):
        cost_label, cost_text = cost_row(s)
        _kv(cost_label, cost_text, GREEN if cost_label == "Credit" else NEUTRAL)
        _kv("Max loss", money_per_contract(s.get("max_loss")), RED)
        _kv("Breakeven", breakeven_text(s.get("breakeven")))
        _kv("Probability", _pct(s.get("pop_pct")), pop_color(s.get("pop_pct")))

    with ui.expansion("Score factors").classes("w-full"):
        if s.get("rr_pct") is not None:
            _kv("Risk / reward", _pct(s.get("rr_pct")))
        if s.get("max_contracts") is not None:
            _kv("Max contracts", str(s.get("max_contracts")))
        if isinstance(s.get("expected_pnl_10"), (int, float)):
            v = s["expected_pnl_10"]
            _kv("Expected P&L (10 contracts)", f"${v:+,.0f}", GREEN if v >= 0 else RED)
        for label, val, known in factor_rows(s.get("factor_scores"), s.get("type"),
                                             s.get("factors_unavailable")):
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                ui.label(label).classes("text-xs w-20 opacity-80")
                ui.html(svg.gradient_bar_svg(val if known else 0))
                ui.label(factor_value_text(val, known)).classes("text-xs w-8 text-right")

    # Greeks / Implied volatility / Expected move: keep the existing three
    # expansions verbatim, except the IV marker guard from Task 8 and the
    # "Implied volatility" rename (whole words, per the UI labelling rule).
```

**Step 4: Run the full webgui suite**

```bash
cd webgui && ../.venv/Scripts/python -m pytest -q
```

Expected: your Step-0 baseline, all green. Remove any test that asserted the deleted `_TILES` grid.

**Step 5: Verify in the browser**

Start the preview on `webgui` (port 8500), open `/`, click a signal. Confirm: flags appear above the contract; the gauge caption is present; money rows read "per contract"; an iron condor shows two breakevens and two leg lines.

If `computer{action:"screenshot"}` times out on the chart-heavy Scanner, read the DOM instead — a documented caveat for this app.

**Step 6: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "feat(detail): triage layout — flags above the fold, units on every figure"
```

---

## Task 12: Flag badge on the collapsed strip

At 44px the panel shows only the toggle, so a flagged trade must still be visible.

**Files:**
- Modify: `webgui/pages/options/detail.py` — `render`, `_Handle`
- Test: `webgui/tests/test_options_detail.py`

**Step 1: Write the failing test**

```python
def test_flag_badge_text_hides_zero():
    assert detail.flag_badge_text(0) == ""
    assert detail.flag_badge_text(2) == "2"
    assert detail.flag_badge_text(12) == "9+"
```

**Step 2: Run to verify it fails**

```bash
cd webgui && ../.venv/Scripts/python -m pytest tests/test_options_detail.py -q -k flag_badge
```

Expected: FAIL.

**Step 3: Implement**

```python
def flag_badge_text(n):
    """Badge label for the collapse toggle; empty hides it. Caps at '9+'."""
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return ""
    return "9+" if n > 9 else str(n)
```

In `render()`, wrap the toggle so the badge can float on it (the same Quasar
`floating`-on-`relative` idiom the nav rail uses):

```python
            with ui.element("div").classes("relative"):
                toggle_btn = ui.button(icon="last_page").props("flat round dense") \
                    .tooltip("Collapse panel")
                with toggle_btn:
                    flag_badge = ui.badge("", color="red").props("floating") \
                        .classes("text-xs")
                flag_badge.set_visibility(False)
```

Add `_set_flag_badge` to `_Handle`:

```python
    def _set_flag_badge(self, n):
        txt = flag_badge_text(n)
        self._flag_badge.text = txt
        self._flag_badge.set_visibility(bool(txt))
```

The badge stays visible when collapsed — do **not** hide it in `toggle()`. That is the entire point.

**Step 4: Run and verify collapse behavior in the browser**

```bash
cd webgui && ../.venv/Scripts/python -m pytest -q
```

Then collapse the panel on a flagged signal and confirm the count is still visible.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git status --short
git commit -m "feat(detail): flag count badge survives panel collapse"
```

---

## Task 13 (optional, additive): emit `factors_unavailable` from Tier 2

Page-side, `em`/`trend`/`gex`/`dex` provenance rests on the exact-50.0 sentinel — suggestive, not proof. Emitting the truth from Tier 2 makes it exact.

**Only do this after Tasks 1–12 are green.** The panel already degrades correctly without it.

**Files:**
- Modify: `services/options_svc/compute.py` (where signals are scored)
- Test: `services/options_svc/tests/test_compute.py`

**Approach:** where factor scores are assembled, record which normalizers received `None` inputs and attach `factors_unavailable: [...]` to the signal. Purely additive — no existing key changes, so the contract stays backward-compatible and an older cached payload still renders.

**Test command** (note: run services one folder at a time; running `pytest services` over all of them re-triggers the documented `config`/`scoring` module-name collisions):

```bash
.venv/Scripts/python -m pytest services/options_svc -q
```

**Restart `options_svc`** for the change to reach the GUI.

---

## Final verification

```bash
cd webgui && ../.venv/Scripts/python -m pytest -q
```

Expected: baseline count, fully green, including `test_no_inline_style.py`.

Then walk all four surfaces in the browser — `/` (Scanner), `/options/swing`, `/options/paper`, `/options/captured` — and for each confirm: the gauge caption names its metric; money rows say "per contract"; a signal with absent factors shows "—" rather than 0 or 50. On Paper specifically, confirm Credit and Max loss are now the same unit.

Use @superpowers:verification-before-completion before claiming done.
