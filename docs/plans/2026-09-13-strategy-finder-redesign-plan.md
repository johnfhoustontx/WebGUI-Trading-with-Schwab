# Strategy Finder Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild `/options/swing` as a scan bar with expiry presets and a risk
style, a summary strip, instant-filter strategy chips, four top-pick cards and a slim
ranked list — with payoff shapes and risk/reward/odds bars.

**Architecture:** The options service adds two additive fields per candidate
(`payoff_curve`, `group`) in `compute.swing_scan`. A new PURE Tier-1 module
`webgui/pages/options/finder_view.py` owns every formatting, preset, chip, pick, bar
and SVG decision (unit-tested). `swing.py` becomes widgets and wiring. The shared
detail panel and hand-offs are reused unchanged; `strategy_table.strategy_columns()`
stays for the Market Scanner's Directional tab.

**Tech Stack:** Python 3.11, NiceGUI/Quasar (Tier 1, Tailwind-only), pytest.
Design: `docs/plans/2026-09-13-strategy-finder-redesign-design.md`.

---

## Ground rules

- `$PY` = `D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe`. options-scanner
  tests from inside `options-scanner/` with `-p no:randomly`; webgui tests from
  inside `webgui/`; services from the repo root, one at a time. Foreground runs.
- TDD. Never weaken an existing assertion unless a task explicitly authorises it.
- Tier 1: Tailwind classes only (no `.style()`), no engine imports; `ui.html` is
  sanitised by DOMPurify, Quasar table `v-html` slots are not (our own strings only).
- Commits end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

### Revision recorded while planning

**Risk-style presets match today's default.** The design's 0.10–0.15 / 0.15–0.25 /
0.25–0.35 would have relabelled today's default band (put −0.20..−0.10, call
0.10..0.20) as *Custom* and silently changed the default scan. Presets are
**Conservative 0.05–0.10 · Balanced 0.10–0.20 (default) · Aggressive 0.20–0.30**,
both sides. The design doc records it.

---

### Task 1: `strategy_scanner.payoff_curve`

**Files:** `options-scanner/strategy_scanner.py`; test `options-scanner/tests/test_strategy_scanner.py`.

**Step 1 — failing tests** (append; reuse `_leg`, `_stock`, `_exp`, `_cal_legs` helpers already in the file):

```python
def test_payoff_curve_long_call_is_flat_then_rising_per_contract():
    legs = [_leg("call", "long", 100.0, 3.0)]
    pts = ss.payoff_curve(legs, spot=100.0, atm_iv=0.28, dte=30, n=25)
    assert len(pts) == 25
    xs = [p[0] for p in pts]
    assert xs == sorted(xs) and xs[0] < 100.0 < xs[-1]
    low, high = pts[0][1], pts[-1][1]
    assert abs(low - (-300.0)) < 0.01          # below the strike: lose the debit
    assert high > 0


def test_payoff_curve_spans_two_expected_moves_to_the_front_expiry():
    import math
    legs = [_leg("put", "long", 100.0, 3.0)]
    pts = ss.payoff_curve(legs, spot=100.0, atm_iv=0.28, dte=30, n=25)
    em = 100.0 * 0.28 * math.sqrt(30 / 365)
    assert abs(pts[0][0] - (100.0 - 2 * em)) < 0.02
    assert abs(pts[-1][0] - (100.0 + 2 * em)) < 0.02


def test_payoff_curve_values_a_calendar_at_the_front_expiry():
    short, long_ = _cal_legs()
    pts = ss.payoff_curve([short, long_], spot=100.0, atm_iv=0.28, dte=14, n=25)
    peak = max(pts, key=lambda p: p[1])
    assert abs(peak[0] - 100.0) < 3.0 and peak[1] > 0


def test_payoff_curve_covered_call_includes_the_shares():
    call = _leg("call", "short", 105.0, 1.0)
    call["expiration"] = _exp(30)
    pts = ss.payoff_curve([_stock(100.0), call], spot=100.0, atm_iv=0.28, dte=30, n=25)
    assert pts[0][1] < -500                     # shares lose below spot
    assert abs(pts[-1][1] - 600.0) < 0.5        # capped at strike - spot + credit


def test_payoff_curve_refuses_unusable_inputs_quietly():
    bad = [_leg("call", "short", 100.0, 1.0, iv=-999.0), _leg("call", "long", 100.0, 2.0, iv=-999.0)]
    bad[0]["expiration"], bad[1]["expiration"] = _exp(7), _exp(35)
    assert ss.payoff_curve(bad, spot=100.0, atm_iv=0.28, dte=7) is None
    assert ss.payoff_curve([_leg("call", "long", 100.0, 3.0)], spot=None, atm_iv=0.28, dte=30) is None
```

**Step 2** — run; expect `AttributeError: payoff_curve`.

**Step 3 — implement** (after `pop_from_payoff`):

```python
def payoff_curve(legs, spot, atm_iv, dte, n=25, width_moves=2.0):
    """``n`` ``[price, pnl_per_contract]`` points across spot ± ``width_moves`` ×
    the expected move to the front expiry, for the Strategy Finder's payoff shapes.

    Valued exactly as ``payoff_metrics`` values the position (``_pl_at`` with the
    same front-expiry rule) and GROSS of commission - it is a shape, not a
    number the page quotes. ``None`` when the position cannot be valued (no spot,
    or a later leg with an unusable IV, which ``_front_value`` refuses): the page
    then draws no shape rather than a made-up one.
    """
    try:
        s = float(spot)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(s) or s <= 0:
        return None
    iv = atm_iv if isinstance(atm_iv, (int, float)) and math.isfinite(atm_iv) and atm_iv > 0 else 0.20
    move = s * iv * math.sqrt(max(int(dte or 0), 1) / 365.0)
    lo, hi = max(s - width_moves * move, 0.0), s + width_moves * move
    entry_cost = sum(_sign(l) * l["mark"] * l.get("qty", 1) for l in legs)
    front = _front_expiration(legs) if _needs_front_valuation(legs) else None
    try:
        return [[round(lo + (hi - lo) * i / (n - 1), 2),
                 round(_pl_at(legs, entry_cost, lo + (hi - lo) * i / (n - 1), front)
                       * _CONTRACT_MULT, 2)]
                for i in range(n)]
    except ValueError:
        return None
```

**Step 4** — run the file; all pass. **Step 5** — commit `feat(finder): a payoff curve for each candidate's shape`.

---

### Task 2: `swing_scan` stamps `group` and attaches `payoff_curve`

**Files:** `services/options_svc/compute.py` (`swing_scan`); test `services/options_svc/tests/test_compute.py`.

**Step 1 — failing tests** (reuse `_patch_swing_inputs`, `_bs_ladder_chain`, `unfiltered_swing` from the file; read them first):

```python
def test_every_swing_candidate_names_the_group_that_built_it(monkeypatch, unfiltered_swing):
    _patch_swing_inputs(monkeypatch, _bs_ladder_chain())
    out = compute.swing_scan("SPY", 5, 60, -0.20, -0.10, 0.10, 0.20, 0.10)
    groups = {s["type"]: s.get("group") for s in out["signals"]}
    assert groups.get("LONG_CALL") == "DIRECTIONAL"
    assert groups.get("BULL_CALL") == "VERTICAL"
    assert groups.get("LONG_STRADDLE") == "STRADDLE"
    assert groups.get("BUTTERFLY_CALL") == "BUTTERFLY"
    assert groups.get("CALENDAR_CALL") == "CALENDAR"
    assert groups.get("COVERED_CALL") == "STOCK"
    assert all(s.get("group") for s in out["signals"])


def test_every_emitted_swing_candidate_carries_a_payoff_curve(monkeypatch, unfiltered_swing):
    _patch_swing_inputs(monkeypatch, _bs_ladder_chain())
    out = compute.swing_scan("SPY", 5, 60, -0.20, -0.10, 0.10, 0.20, 0.10)
    for s in out["signals"]:
        curve = s.get("payoff_curve")
        assert curve is None or (len(curve) == 25 and all(len(p) == 2 for p in curve)), s["type"]
    assert any(s.get("payoff_curve") for s in out["signals"])
```

(If `_bs_ladder_chain` or `_patch_swing_inputs` have different names, use the real ones. If a credit spread adapted from `screen_spreads` is stubbed empty in that helper, add a PCS/IC `group` assertion only where the fixture builds one.)

**Step 3 — implement** in `swing_scan`:
- Beside the families constant add `_tag(batch, group)` that sets `s["group"] = group` on each dict and returns the list; wrap every builder result: `signals += _tag(ssn.build_directional(...), "DIRECTIONAL")`, adapted credit spreads and debit verticals → `"VERTICAL"`, iron condors → `"NEUTRAL"`, and the four new groups by name.
- After `assign_ids(...)` (only emitted rows), attach `s["payoff_curve"] = ssn.payoff_curve(s.get("legs") or [], spot, atm_iv, s.get("dte"))` for each signal.
- Comment: additive display fields; `group` is the build group, distinct from the scoring `family`.

**Step 4** — `$PY -m pytest services/options_svc -q -rf` (repo root) and `test_income_scan.py` unchanged. **Step 5** — commit `feat(finder): each candidate names its group and carries a payoff curve`.

---

### Task 3: `finder_view.py` — formatting, presets, chips, picks, summary

**Files:** create `webgui/pages/options/finder_view.py`; test `webgui/tests/test_finder_view.py`.

**Step 1 — failing tests:**

```python
from pages.options import finder_view as fv


def test_money_formats_by_size():
    assert fv.money(54057.72) == "$54,058"
    assert fv.money(195.34) == "$195"
    assert fv.money(4.5) == "$4.50"
    assert fv.money(None) == "—"
    assert fv.money(float("nan")) == "—"


def test_cost_text_names_credit_debit_and_shares():
    assert fv.cost_text({"net_debit": 195.34}) == "$195 debit"
    assert fv.cost_text({"net_credit": 803.67}) == "$804 credit"
    shares = {"net_debit": 54057.72, "legs": [{"kind": "stock"}]}
    assert fv.cost_text(shares) == "$54,058 debit for 100 shares"
    assert fv.cost_text({}) == "—"


def test_expiry_text():
    assert fv.expiry_text({"expiration": "2026-10-16", "dte": 8}) == "Oct 16 · 8d"
    assert fv.expiry_text({}) == "—"


def test_expiry_presets_round_trip_and_detect_custom():
    assert [p[0] for p in fv.EXPIRY_PRESETS] == ["1–2 wk", "2–6 wk", "1–3 mo", "Any"]
    for label, lo, hi in fv.EXPIRY_PRESETS:
        assert fv.expiry_preset_for(lo, hi) == label
    assert fv.expiry_preset_for(3, 9) is None


def test_risk_styles_match_todays_default_as_balanced():
    assert fv.RISK_DEFAULT == "Balanced"
    b = fv.risk_bands("Balanced")
    assert b == {"put_d_min": -0.20, "put_d_max": -0.10, "call_d_min": 0.10, "call_d_max": 0.20}
    assert fv.risk_style_for(**b) == "Balanced"
    assert fv.risk_style_for(**fv.risk_bands("Aggressive")) == "Aggressive"
    assert fv.risk_style_for(-0.30, -0.12, 0.10, 0.20) == "Custom"


def _sig(t, g, score, **kw):
    return {"id": t, "type": t, "group": g, "composite_score": score, **kw}


def test_chip_counts_follow_group_order_and_skip_empty():
    sigs = [_sig("A", "VERTICAL", 70), _sig("B", "VERTICAL", 60), _sig("C", "CALENDAR", 65)]
    assert fv.chip_counts(sigs) == [("VERTICAL", "Spreads", 2), ("CALENDAR", "Calendars", 1)]


def test_filter_by_groups():
    sigs = [_sig("A", "VERTICAL", 70), _sig("C", "CALENDAR", 65)]
    assert [s["id"] for s in fv.filter_groups(sigs, None)] == ["A", "C"]
    assert [s["id"] for s in fv.filter_groups(sigs, {"CALENDAR"})] == ["C"]
    assert fv.filter_groups(sigs, set()) == []


def test_top_picks_take_the_best_of_distinct_groups():
    sigs = [_sig("A", "VERTICAL", 80), _sig("B", "VERTICAL", 79), _sig("C", "CALENDAR", 70),
            _sig("D", "STOCK", 75), _sig("E", "BUTTERFLY", 60), _sig("F", "STRADDLE", 55)]
    assert [s["id"] for s in fv.top_picks(sigs, k=4)] == ["A", "D", "C", "E"]
    assert [s["id"] for s in fv.top_picks(sigs[:2], k=4)] == ["A"]


def test_summary_facts():
    payload = {"symbol": "SPY", "filtered_out": 6, "vol_filtered": 0,
               "view": {"direction": "neutral", "conviction": 0.1, "vol_regime": "mid"},
               "signals": [{"underlying_price": 540.12, "iv_rank": 55.0}] * 16}
    f = fv.summary_facts(payload)
    assert f["symbol"] == "SPY" and f["price"] == "$540.12"
    assert f["pills"] == ["Neutral", "Conviction low", "Volatility mid"]
    assert f["vol_rank"] == "Vol Rank 55"
    assert f["counts"] == "16 ideas · 6 below the quality bar"
    assert fv.summary_facts({}) is None
```

**Step 3 — implement** `finder_view.py` (pure, imports nothing from `nicegui`):
- `GROUPS` — ordered `(code, label)`: DIRECTIONAL Directional · VERTICAL Spreads · NEUTRAL Neutral · STRADDLE Straddles & strangles · BUTTERFLY Butterflies & condors · CALENDAR Calendars · STOCK Stock + options. `swing._FAMILY_OPTIONS` should become an alias of this (Task 5).
- `money(v)`: non-finite/None → "—"; `|v| ≥ 100` → `$` + thousands, no decimals; else two decimals. Use `pages.fmt.num`.
- `cost_text(sig)`, `expiry_text(sig)` (month abbreviation + day · `{dte}d`).
- `EXPIRY_PRESETS = [("1–2 wk", 7, 14), ("2–6 wk", 14, 42), ("1–3 mo", 30, 90), ("Any", 0, 120)]`; `expiry_preset_for(lo, hi)`.
- `RISK_STYLES = {"Conservative": (0.05, 0.10), "Balanced": (0.10, 0.20), "Aggressive": (0.20, 0.30)}`, `RISK_DEFAULT = "Balanced"`; `risk_bands(name)` → the four keyword values (put side negative: `put_d_min=-hi, put_d_max=-lo`); `risk_style_for(put_d_min, put_d_max, call_d_min, call_d_max)` with a 1e-9 tolerance → name or "Custom".
- `chip_counts(signals)` → list of `(code, label, n)` in `GROUPS` order, n > 0 only; `filter_groups(signals, active)` (`None` = all).
- `top_picks(signals, k=4)`: sort by `composite_score` desc (None last, then `id` for determinism); take the first signal of each group not yet taken, until k.
- `summary_facts(payload)`: `None` when there is no `symbol`; price from the first signal's `underlying_price`; pills — direction title-case, conviction `low` < 0.34 ≤ `medium` < 0.67 ≤ `high`, volatility regime; `vol_rank` "Vol Rank {int}" or None; counts `"{n} ideas"` + `" · {k} below the quality bar"` + `" · {j} where premium is too cheap to sell"` when non-zero.

**Step 4** — run; pass. **Step 5** — commit `feat(finder): the pure view model for the redesigned page`.

---

### Task 4: bars and payoff SVG

**Files:** `webgui/pages/options/finder_view.py`; test `webgui/tests/test_finder_view.py`.

**Step 1 — failing tests:**

```python
def test_split_bar_scales_loss_and_profit_to_the_larger():
    b = fv.risk_reward_bar({"max_loss": 200.0, "max_profit": 800.0})
    assert b["loss_class"] == "w-[25%]" and b["profit_class"] == "w-full"
    b = fv.risk_reward_bar({"max_loss": 53817.0, "max_profit": 1960.0})
    assert b["loss_class"] == "w-full" and b["profit_class"] == "w-[5%]"


def test_split_bar_unbounded_profit_is_full_and_marked():
    b = fv.risk_reward_bar({"max_loss": 500.0, "max_profit": None, "unbounded_profit": True})
    assert b["profit_class"] == "w-full" and b["profit_label"] == "∞"


def test_split_bar_missing_numbers_draws_nothing():
    assert fv.risk_reward_bar({}) is None


def test_pop_bar_snaps_and_colours_by_band():
    assert fv.pop_bar(46.4) == {"class": "w-[45%]", "tone": "neutral", "label": "46%"}
    assert fv.pop_bar(31.0)["tone"] == "warn"
    assert fv.pop_bar(72.0)["tone"] == "pos"
    assert fv.pop_bar(None) is None


def test_payoff_svg_is_fixed_size_and_colours_by_sign():
    curve = [[90.0, -300.0], [100.0, -300.0], [110.0, 700.0]]
    svg = fv.payoff_svg(curve, spot=100.0, width=120, height=32)
    assert svg.startswith("<svg") and 'width="120"' in svg and 'height="32"' in svg
    assert "preserveAspectRatio" not in svg and "vector-effect" not in svg
    assert fv.PROFIT_STROKE in svg and fv.LOSS_STROKE in svg
    assert fv.payoff_svg(None, 100.0) == "" and fv.payoff_svg([[1, 2]], 100.0) == ""


def test_payoff_svg_emits_nothing_dompurify_would_strip():
    import re
    from test_rings import _dompurify_allowlist
    allow = _dompurify_allowlist()
    svg = fv.payoff_svg([[90.0, -300.0], [100.0, -300.0], [110.0, 700.0]], spot=100.0)
    for tag in re.findall(r"<([a-zA-Z][\w-]*)", svg):
        assert tag.lower() in allow, tag
    for attr in re.findall(r'\s([a-zA-Z][\w:-]*)="', svg):
        assert attr.lower() in allow, attr
```

(Read `webgui/tests/test_rings.py::_dompurify_allowlist` for its return shape and adapt the two loops to it — mirror `test_ring_svg_emits_nothing_dompurify_would_strip`.)

**Step 3 — implement:**
- `_WIDTH = {p: ("w-0" if p == 0 else "w-full" if p == 100 else f"w-[{p}%]") for p in range(0, 101, 5)}`; `_snap(pct)` → nearest 5 in [0, 100]; a positive value never snaps to 0 (min 5).
- `risk_reward_bar(sig)` → `{"loss_class", "profit_class", "loss_label", "profit_label"}` (labels via `money`; unbounded profit → "∞" and full), `None` when neither number is usable.
- `pop_bar(pop)` → `{"class", "tone": "warn" (<40) | "neutral" | "pos" (>60), "label": f"{round(pop)}%"}`.
- `PROFIT_STROKE = "#34d399"`, `LOSS_STROKE = "#f87171"`, `ZERO_STROKE = "#3a4a6b"`, `SPOT_STROKE = "#8794b4"` (the app's existing P/L colours; chart colours are out of scope of the class rule).
- `payoff_svg(curve, spot, width=120, height=32)`: `""` for fewer than 2 points; map x by price range and y by pnl range (include 0 in the range), pad 2 px; one `<line>` per segment coloured by the sign of the segment's mid P&L; a dashed zero `<line>`; a short spot tick `<line>`; `<svg width height viewBox="0 0 W H" xmlns=...>` only.

**Step 4** — run; pass. **Step 5** — commit `feat(finder): risk, reward and odds bars, and the payoff shape`.

---

### Task 5: rebuild `swing.py`

**Files:** `webgui/pages/options/swing.py`; `webgui/pages/options/finder_view.py` (list columns/rows); tests `webgui/tests/test_options_swing.py`, `webgui/tests/test_finder_view.py`, `webgui/tests/test_no_inline_style.py` (confirm swing is covered).

Read first: `swing.py`, `strategy_table.py` (`strategy_rows`, `detail_signal`, `_PAPER_TYPES`, slots), `detail.py` (`render`, `_Handle` open/collapse state), `handoff.add_strategy_row_actions` and its slot, `pages/busy.py`, `theme.py` tokens (`CARD`, `EYEBROW`, `MUTED`, `LABEL`, `BTN`, `BTN_3D`, `BADGE_*`, `TXT_*`).

**Step 1 — failing tests** (`test_finder_view.py`):

```python
def test_finder_columns_fit_without_the_old_extras():
    names = [c["name"] for c in fv.finder_columns()]
    assert names == ["strategy", "composite_score", "expiry", "cost", "max_profit",
                     "max_loss", "pop", "grade", "actions"]


def test_finder_rows_carry_shape_bars_and_paper_gate():
    sig = {"id": "x", "type": "BUTTERFLY_CALL", "group": "BUTTERFLY", "strategy_label": "Call Butterfly",
           "composite_score": 72.1, "grade": "Good", "expiration": "2026-10-16", "dte": 30,
           "net_debit": 120.0, "max_profit": 374.8, "max_loss": 125.2, "pop_pct": 31.5,
           "payoff_curve": [[90, -120], [100, 380], [110, -120]], "underlying_price": 100.0}
    row = fv.finder_rows([sig])[0]
    assert row["strategy"] == "Call Butterfly" and row["cost"] == "$120 debit"
    assert row["_payoff_svg"].startswith("<svg") and row["_allow_paper"] is True
    assert row["_pop"]["label"] == "32%"
```

and in `test_options_swing.py`, **authorised copy change**: `status_text` now reads `"{n} ideas."` instead of `"{n} swing signals."` — update ONLY the literal strings in the existing `status_text` tests accordingly (the sentences about the quality bar / cheap premium are unchanged). If you instead move all status wording to `finder_view.summary_facts` and delete `status_text`, delete its tests only with this authorisation and note it in the commit.

**Step 3 — implement:**
- `finder_view.finder_columns()` (sortable where meaningful) and `finder_rows(signals)` → each row: `id`, `strategy` (label), `composite_score`, `expiry` (`expiry_text`), `cost`, `max_profit` (∞ when unbounded), `max_loss` (∞ + "undefined risk" flag when unbounded loss), `pop` (label), `grade`, `grade_reason`, `_score_class` (reuse `scanner.score_zone_class`), `_grade_class` (reuse `strategy_table.grade_class`), `_payoff_svg` (72×20), `_rr` (`risk_reward_bar`), `_pop` (`pop_bar`), `_allow_paper` (reuse `strategy_table._PAPER_TYPES`), `_undefined_risk`.
- `swing.py` layout (Tailwind tokens only):
  1. **Scan bar** (`CARD`): Symbol input; expiry preset button group + DTE min/max (a preset click sets both boxes; editing a box refreshes the highlighted preset via `expiry_preset_for`); risk-style toggle (`ui.toggle` over `RISK_STYLES` + a read-only *Custom* state when `risk_style_for` returns it) that writes the four Advanced delta fields; Scan button (`BTN_3D`); Advanced expansion keeps the raw delta and credit fields (editing them re-evaluates the toggle). Enter / tab-out still scans (`bind_symbol_load`). The request no longer sends `families`.
  2. **Summary strip**: symbol + price, pills, Vol Rank, counts — from `summary_facts`; hidden when `None`.
  3. **Chips**: `All` + one chip per `chip_counts` entry; active chips highlighted (`BADGE_ACCENT` vs `BADGE_MUTED` via `classes(remove=…, add=…)`); clicking toggles membership and repaints cards + list from the cached signals without a scan.
  4. **Top picks**: up to four `CARD`s from `top_picks(filter_groups(...))`; each shows name, score badge, grade, `expiry_text`, `strategy_table.legs_summary(legs)`, `ui.html(payoff_svg(curve, spot))` (120×32), the split bar (two `div`s with `loss_class` / `profit_class` inside a track, labels beneath) and the odds bar, `cost_text`, and buttons: Calculator (`handoff.send_signal_to_calculator`) and — when allowed — Paper (`handoff.send_to_paper`). Clicking the card body selects it in the detail panel.
  5. **Ranked list**: `ui.table(columns=finder_columns(), rows=finder_rows(visible))`, full width, no internal height cap; slots: `body-cell-strategy` (`<span v-html="props.row._payoff_svg"></span>` + label), `body-cell-composite_score` (badge, as today), `body-cell-max_loss` (value + undefined-risk badge, as today), `body-cell-pop` (a small track + `props.row._pop.class` fill + label), `body-cell-grade` (as today, with tooltip); actions via `handoff.add_strategy_row_actions(table, lambda row: by_id.get(row.get("id")))`.
  6. **Detail panel**: `detail.render()` beside the list; start collapsed and open it on the first selection using the handle's existing open/toggle mechanism (read `detail.py`; if no public way exists, add a minimal `open()` to `_Handle` with a test in `test_options_detail.py` — it must not change other pages' defaults).
  - **Loading**: on Scan, show four grey placeholder cards reading "Scanning {SYMBOL}…" and clear the list; keep `pages.busy` for the list area.
  - **Empty/cold**: before any payload, one line ("Enter a symbol and press Scan to rank every strategy for it."); cold service → the shared `pages.copy.WAITING_OPTIONS` line (check its real name).
  - **Repaint**: the existing version-poll timer; chip state survives repaints (reset to All only when the symbol changes).
- Keep `pct_to_fraction`; `_FAMILY_OPTIONS` may be removed if nothing else imports it (grep; `test_strategy_table.py` pins labels against it — if it does, keep it as `dict(finder_view.GROUPS)`).

**Step 4** — `cd webgui && $PY -m pytest tests/test_finder_view.py tests/test_options_swing.py tests/test_strategy_table.py tests/test_options_detail.py tests/test_options_scanner.py tests/test_no_inline_style.py tests/test_page_help.py -q -rf`. **Step 5** — commit `feat(finder): the redesigned Strategy Finder page`.

---

### Task 6: visual verification in the local harness

**Files:** none in the repo (scratch harness + a TEMPORARY `.claude/launch.json` entry, reverted).

Reuse `scratchpad/harness/finder_harness.py` (fake bus, real handlers, synthetic chain). Launch, scan SPY, and check at 1440 px and 1024 px: no console errors; cards, chips, summary, list; chip filtering is instant; presets and risk style update the fields; payoff shapes and bars render and read correctly for a butterfly, a calendar, a collar, a long call; detail panel opens on click and closes; loading placeholders on rescan. Capture before/after screenshots and fix anything wrong in a follow-up commit (TDD where the fix is logic). Revert launch.json.

---

### Task 7: docs

- `webgui/page_help.py` `/options/swing`: the new layout, chips (instant filters), presets, risk style, cards, bars.
- `docs/manuals/user-guide/user-guide.md` and `reference-guide.md` Strategy Finder sections; `docs/webgui-routes.md`; `docs/CHANGELOG.md` top entry. Rebuild changed manuals only.
- Note in the CHANGELOG that the marketing gallery shot (`tools/gallery_screens.py` image19) is stale until the next capture run.

Run `cd webgui && $PY -m pytest tests/test_page_help.py tests/test_docs_cover_the_ui.py -q -rf`. Commit `docs(finder): the redesigned page`.

---

### Task 8: full verification

Run options-scanner, webgui, services/options_svc, shared/tests, tools/tests (compare failing sets), ruff; then superpowers:requesting-code-review on `45b91b3..HEAD`.
