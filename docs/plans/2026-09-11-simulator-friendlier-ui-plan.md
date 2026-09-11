# Simulator — a friendlier screen: Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `/options/simulator` state its numbers instead of hiding them behind
chart hovers, and fix the IV-shock and Replay views (design:
[2026-09-11-simulator-friendlier-ui-design.md](2026-09-11-simulator-friendlier-ui-design.md)).

**Architecture:** All new arithmetic goes in a new PURE module
`webgui/pages/options/sim_view.py` (no `nicegui` import), unit-tested with sample
dicts. `simulator.py` holds widgets and wiring only. Two Tier-2 changes in
`services/options_svc/compute.py`: position units + a `units` marker on
`sim_run.ivshock`, and `value`/`pnl` + position-unit Greeks on `sim_replay`.

**Tech Stack:** NiceGUI 3 (Tailwind tokens from `pages/options/theme.py`),
Highcharts option dicts, pytest.

**Test commands (Windows worktree — the venv is the main checkout's):**

```bash
PY="$(pwd)/../../../.venv/Scripts/python.exe"
(cd webgui && "$PY" -m pytest -q -p no:randomly tests/test_sim_view.py tests/test_options_simulator.py)
"$PY" -m pytest -q -p no:randomly services/options_svc/tests/test_compute.py -k sim_
```

---

### Task 1: `sim_view` — expiry payoff facts

**Files:** Create `webgui/pages/options/sim_view.py`; Test `webgui/tests/test_sim_view.py`

Functions:
- `payoff_facts(legs, baseline) -> dict` with keys `entry` (signed $, credit > 0),
  `max_profit`, `max_loss` (positive $ or the string `"unlimited"` or `None`),
  `breakevens` (sorted list of prices), `reason` (why a figure is absent, or `None`).
  - entry = `-baseline`; `None` baseline or non-finite ⇒ entry `None`.
  - Mixed expiries ⇒ profit/loss/breakevens `None`, reason `"mixed_expiry"`.
  - A leg with no usable strike/qty ⇒ `None`s, reason `"incomplete"`.
  - Corners `{0} ∪ strikes`; slope above last strike = net call qty × 100.

Tests (write first, see them fail, then implement):
- put credit spread 330/325, qty 10, baseline −1,000 ⇒ entry 1000, max_profit 1000,
  max_loss 4000, breakevens [329.0].
- long call 100, baseline 500 ⇒ entry −500, max_profit "unlimited", max_loss 500,
  breakevens [105.0].
- naked call ⇒ max_loss "unlimited".
- iron condor ⇒ two breakevens, one-side risk.
- mixed expiries ⇒ reason "mixed_expiry", figures None, entry still stated.
- NaN baseline ⇒ entry None (never 0).

Commit: `feat(simulator): expiry payoff facts as a pure module`

### Task 2: `sim_view` — tiles

- `position_tiles(legs, result) -> list[dict]` — always six
  `{key, label, value, sub, tone}`; tone ∈ {"pos","neg","neutral"}.
  Uses `payoff_facts` and `position_greeks(result)` (the ivshock base row in
  position units via Task 5's normalizer). Em-dash for every absence.

Tests: six tiles in fixed order; credit labelled "Entry credit", debit "Entry
debit"; "Unlimited" text; mixed-expiry sub-line names the reason; an empty result
yields six em-dashes.

Commit: `feat(simulator): six position tiles`

### Task 3: `sim_view` — slider readouts and the Days range

- `fractional_dte(expiry, now)` — days to 16:00 America/New_York on `expiry`, ≥ 0.
- `days_range(legs, now) -> {max, step, snaps:[(label, days)]}` — step 1, or 0.25
  when the longest leg is ≤ 3 days; max rounded UP to the step (≥ step); snaps Now /
  Halfway / Expiry-or-First-expiry, each rounded up to the step and ≤ max.
- `days_text(dt)` — `"5 days"`, `"1 day"`, `"6 hours"` under one day, `"Now"` at 0.
- `curve_pnl_at(pairs, x)` — linear interpolation, `None` outside the range.
- `whatif_readout(pairs, target_s, dt, now) -> (text, tone)`.

Tests cover: 0-DTE at 11:00 ET → max 0.25 step, Expiry snap reaches the close;
mixed legs → "First expiry"; interpolation midpoint; out-of-range → None; profit vs
loss wording and tone.

Commit: `feat(simulator): slider readouts and a Days range fitted to the legs`

### Task 4: `sim_view` — structure checks and copy

- `structure_warnings(legs) -> list[str]` — short outlives long (names leg number
  and the long leg's expiry); net short calls.
- `matches_template(code, legs) -> bool` — shape multiset + near/far expiry pattern.
- `empty_state_text(meta, legs) -> str` — no chain / leg without strike / strike not
  listed (names leg, strike, type, expiry) / "Pricing…".

Commit: `feat(simulator): structure warnings, edited check, one empty-state rule`

### Task 5: position units — the page normalizer + the IV-shock table

- `position_units(row, units)` — ×100 on the six Greek/value columns unless
  `units == "position"`.
- `ivshock_table(ivshock, mult) -> {headline, tone, rows:[{label, base, shock,
  change, tone}]}`; headline states the $ change in plain words.

Tests: a legacy payload (no marker) and a marked payload produce identical tables;
headline "loses"/"gains"/"barely changes".

Commit: `feat(simulator): IV shock as a table in position dollars`

### Task 6: service — position units and replay P/L

**Files:** `services/options_svc/compute.py` (`sim_run`, `sim_replay`);
`services/options_svc/tests/test_compute.py`.

- `sim_run`: scale `ivshock.base/shock` numeric Greek columns by `_CONTRACT_MULT`,
  add `"units": "position"`.
- `sim_replay`: add `"value"` (= `theo_price` × 100), `"pnl"` (value − value[0]),
  scale Greeks ×100, add `"units": "position"`.

Update `test_sim_run_returns_whatif_and_ivshock` to the new units (the fake theo 1.0
becomes 100.0 — a spec change, stated in the commit), add a units test and a replay
value/pnl test.

Commit: `feat(options_svc): simulator payloads in position units, replay carries P/L`

### Task 7: replay figure + cursor readout

- `replay_figure(trace, cursor)` — panels Price, Profit / loss, Delta, Gamma,
  Theta per day, Vega; hidden x labels; categories = per-bar timestamps; session
  plotLines labelled with dates (first session included).
- `replay_cursor_text(trace, i)` in `sim_view`.

Update the existing replay-figure tests for the new panel list (subject changed).

Commit: `feat(simulator): replay shows the position's P/L and real dates`

### Task 8: wire the page

`simulator.py`: tiles column, readout line, snap buttons, Days range on meta/legs
change, warnings + Edited chip + Set-all-legs expiry, IV-shock table replacing the
chart, scrubber `props(max=…)` fix defaulting to the latest bar, "Load chain" copy,
`empty_state_text` for both empty labels.

Render tests: tiles mount (six), snap buttons present, scrubber `_props["max"]`
equals `len(x) - 1` after a warm replay, no `ivshock` highchart remains.

Commit: `feat(simulator): wire the readouts, snaps, warnings and fixed scrubber`

### Task 9: docs + full suites + browser check

`page_help.py`, `docs/webgui-routes.md`, User Guide, `docs/CHANGELOG.md`.
Run the full webgui suite and the options_svc suite; compare the failing set.

Commit: `docs(simulator): the guide, routes and changelog describe the new screen`
