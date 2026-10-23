# Whole-session heatmaps with projected contours: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From the open, every Dealer Positioning heatmap (GEX, Charm, DEX, Vanna) spans 08:30–15:00, one column a minute, with projected cells right of "now" and the contour lines traced across both (dashed past "now").

**Architecture:** `options_svc` prices each view's nearest-expiry open interest at future marks (every `[forward] step_min`), restricted to the strikes the history keeps, and publishes it inside each view's history payload. The page turns that into minute rows shaped like history rows, appends them to the collected rows, and runs the ONE existing pipeline (value, change, frame, smoothing, share, scale, contours) over the whole session.

**Tech Stack:** Python 3.11, numpy/scipy (service only), NiceGUI + Highcharts (page), pytest, fakeredis.

**Spec:** [2026-10-23-heatmap-full-session-forward-design.md](2026-10-23-heatmap-full-session-forward-design.md)

## Global Constraints

- Tier 1 (`webgui/`) imports no numpy, no engine. The forward-row helper is plain arithmetic in `webgui/pages/options/gamma_heat.py`.
- New service code goes in `services/options_svc/forward_grid.py`, which imports nothing from `compute`. `compute.py`'s line count must not grow (net).
- The heatmap's series count is fixed in every view, mode and time of day.
- Contour series keep a new `pointStart` on every build (`_CONTOUR_BUILD`).
- `[forward] enabled = true`, `step_min = 5` in `config/gamma_heat.toml`; both in `webgui/config_schema.py`.
- Time to expiry follows the engine: `(dte*24 + hours to 15:00 CT) / (365*24)`, floored 1e-6. Marks stop at one minute before the day's real close.
- A forward grid exists only on a trading day, after the regular open, before (close − 1 min), with `enabled`.
- The Adaptive colour scale and contour levels are set from collected + projected cells together. Locked is unchanged.
- Smoothing covers the whole grid.
- Manuals (`webgui/page_help.py`, User Guide, Technical Reference) change in the same branch.
- Never weaken an existing test to make it pass; a test that pinned the old behaviour is rewritten to pin the new one, with the reason in its docstring.

## Review Focus

1. **Switching symbol or view mid-session**: the forward band must belong to the same symbol as the rows. It rides in the same payload and goes through `history_rows`' symbol check (Task 5 test).
2. **Repaint after the band disappears** (15:00 passes, kill switch off): an in-place update must not keep the old dashed zone or the old "now" line. `zones` and `plotLines` are always emitted (Task 6 test).
3. **Change over N min at a projected column** must compare with the column N minutes earlier by real epoch time, collected or projected (Task 6 test).
4. **Weekend / holiday / pre-market**: no forward data at all, the chart is today's collected-only chart (Task 3 test).
5. **A strike priced in the forward grid but absent from the collected rows** (spot drifted): the combined ladder must still be uniform and no row vanishes (Task 6 test).

---

### Task 1: Config `[forward]` and `bs_charm_v`

**Files:**
- Modify: `shared/gamma_heat_config.py`, `config/gamma_heat.toml`, `webgui/config_schema.py`, `options-scanner/options_calculator.py`
- Test: `shared/tests/test_gamma_heat_config.py`, `options-scanner/tests/test_options_calculator_vectorized.py` (create if no vectorized test file exists; else append to it)

**Interfaces:**
- Produces: `gamma_heat_config.forward() -> {"enabled": bool, "step_min": int}` (step_min in 1..30, default 5; enabled must be a real bool, else default True). `options_calculator.bs_charm_v(S, K, T, r, sigma, option_type="call")` — arrays for S/K/sigma allowed, returns 0 where `T<=0` or `sigma<=0`.

- [ ] Test `test_forward_defaults` (`{"enabled": True, "step_min": 5}`), `test_forward_refuses_a_bad_step` (0, 31, `True`, `"5"` → 5), `test_forward_enabled_must_be_a_bool` (`1` → True default, `False` → False). Monkeypatch `load` like the file's other tests.
- [ ] Test `test_bs_charm_v_matches_scalar`: for K in (90, 100, 110), T in (1e-4, 0.01, 0.1), sigma in (0.1, 0.4), both types, `bs_charm_v(100, np.array([K]), T, r, np.array([sigma]))[0] == pytest.approx(bs_charm(...), rel=1e-9)`; `sigma=0` → 0.
- [ ] Run both, see them fail. Implement. Add `[forward]` to the toml with the design's comment, and a "Projected afternoon" Section to `_GAMMA_HEAT` with `forward.enabled` (`kind="bool"`) and `forward.step_min` (`kind="int"`, unit min, 1..30).
- [ ] Run `shared/tests/test_gamma_heat_config.py`, the calculator test, `webgui/tests/test_config_schema.py`: PASS. Commit `feat(gamma): [forward] config and vectorised charm`.

### Task 2: `forward_grid.py` — marks and per-view grids

**Files:**
- Create: `services/options_svc/forward_grid.py`
- Test: `services/options_svc/tests/test_forward_grid.py`

**Interfaces:**
- Produces:
  - `forward_marks(now: datetime, step_min: int) -> list[datetime]` — CT-aware. `[]` unless `market_calendar.is_trading_day(now.date())`, `market_calendar.regular_session_has_opened(now)`, and `now < regular_close_on(today) - 1 min`. Otherwise `[now, every clock multiple of step_min strictly after now and strictly before last, last]` where `last = regular_close_on(today) - 1 min`.
  - `build(chain: dict, spot: float, marks: list[datetime], strikes: dict[str, set[float]]) -> dict[str, dict]` — keys among `"GEX","Charm","DEX","Vanna"` (only those in `strikes` with any priced contract). Each value: `{"ts": [epoch int], "times": ["HH:MM"], "spot": spot, "call": {str(float strike): [float per mark]}, "put": {...}}`, values through `gex_history_db._round_sig`. A strike with a contract on one side only gets `0.0` on the other (matches `_ensure`). Empty dict on no marks / no spot / no nearest expiry. Never raises (a broad guard calls `_degrade.degraded("options.forward_grid")`).
- Rules (copy `calc_all_from_chain`'s units and signs exactly; nearest expiry via `gamma_tool.GammaEngine._find_nearest_exp_key` with `today = marks[0]` CT date; `dte = min(call, put)`; weight `openInterest`; `T(m) = max((dte*24 + max(0, 15 - m.hour - m.minute/60))/(365*24), 1e-6)`; `T_now = T(marks[0])`; `r = RISK_FREE_RATE`):
  - GEX: `±_gamma(c)*oi*100*S²*0.01` (put negative) × `bs_gamma_v(T')/bs_gamma_v(T_now)`; ratio 1 where `iv<=0` or denominator 0.
  - Charm: `bs_charm_v(T')*oi*100*S` for `iv>0` only, put NOT negated.
  - Vanna: `bs_vanna_v(T')*oi*100*S` for `iv>0` only.
  - DEX: `oi*100*S*(d_chain + bs_delta_v(T') - bs_delta_v(T_now))` when the chain delta is usable (`_delta(c)` not None and != 0) and `iv>0`; `d_chain` flat when `iv<=0`; when unusable, `bs_delta_v(T')` if `iv>0` else 0.

- [ ] Tests (CT dates; 2026-10-23 is a Friday trading day, 2026-10-24 Saturday, 2026-11-26 Thanksgiving, 2026-11-27 a 12:00 CT half day — check `regular_close_on` for that date and use whatever half day the calendar knows):
  - `test_marks_from_the_open`: 08:31:20 → first is now, second 08:35, last 14:59, all between are :x0/:x5.
  - `test_no_marks_before_the_open_on_a_weekend_on_a_holiday_or_from_the_last_minute` (08:20, Sat 10:00, Thanksgiving 10:00, 14:59:30 → `[]`).
  - `test_half_day_stops_a_minute_before_its_close`.
  - `test_seam_equals_the_collected_cell`: on a Schwab-shaped 0-DTE chain (reuse `_chain` shape from `test_projection.py`, add a `delta`), with `calc_all_from_chain(chain, now=marks[0])`, every view's `call[k][0]` and `put[k][0]` equal the engine's `{call, put}` at k, `rel=1e-6`.
  - `test_0dte_atm_gamma_and_charm_grow_into_the_close` (|last| > |first| at ATM for GEX and Charm).
  - `test_only_the_given_strikes_are_priced` and `test_a_view_not_asked_for_is_absent`.
  - `test_no_iv_holds_gex_and_dex_flat_and_drops_charm`.
  - `test_bad_chain_gives_empty` (`{}`, spot 0, no expirations).
- [ ] Run, fail, implement, pass (`.venv\Scripts\python -m pytest services/options_svc/tests/test_forward_grid.py -q` from the repo root). Commit `feat(options): forward grids for all four heatmap views`.

### Task 3: Attach in `gamma_snapshot`, retire `projection`, publish with history

**Files:**
- Modify: `services/options_svc/compute.py` (`gamma_snapshot`, `_crop_gamma_views`), `services/options_svc/handlers.py` (`_publish_gamma`)
- Test: `services/options_svc/tests/test_compute.py` (rewrite the two `projection` tests near lines 2007 and 2481), `services/options_svc/tests/test_handlers.py` or `test_gamma_published.py`

**Interfaces:**
- Consumes: `forward_grid.forward_marks`, `forward_grid.build`, `gamma_heat_config.forward()`.
- Produces: each view entry may carry `"forward": {..., "cone": {"mid","up","down"}}` (cone from `project_em_cone(spot, atm_iv_from_chain(chain, spot), marks, now)`, same for every view). No entry carries `"projection"`. Each history payload is `{"symbol", "view", "rows", "forward"}`, `forward` None when absent. The main key carries no `forward`.
- Placement: after `_crop_gamma_views(views, spot)`, strikes per view = float keys of the cropped `data["gex"]` ∪ every cropped history row's grid keys. Delete the GEX-only projection attach and `_crop_gamma_views`' projection block (keep `project_gex_grid`, `_future_marks_ct`, `project_em_cone` for `_projection_brief` and their tests). Net line count of `compute.py` must not rise: move what is needed into `forward_grid`.

- [ ] Rewrite `test_compute.py`'s projection tests as `test_snapshot_attaches_forward_to_every_view_in_session` (patch the clock the way the existing test does, to a Friday 10:00 CT) and `test_snapshot_has_no_forward_on_a_saturday` and `test_kill_switch_drops_forward`.
- [ ] Add `test_forward_rides_in_the_history_payload_not_the_main_key`: publish a snapshot with forward on GEX; `cache_get(gamma_history_key("GEX")).payload["forward"]` equals it; the main payload's GEX entry has no `forward`; a view with none gets `forward: None`.
- [ ] Run, fail, implement, then run the whole `services/options_svc` suite: `.venv\Scripts\python -m pytest services/options_svc -q -rf`. Compare the failing SET with a baseline taken before Task 1. Commit `feat(options): publish each view's forward band with its history`.

### Task 4: Measure the service cost

- [ ] With a real `$SPX` chain from the E: drive backup or `scalp.db`-era snapshot if no live chain is reachable (memory: *Measure on the E: drive prod backup*), time `forward_grid.build` at 08:31 for all four views over the cropped strike set: budget < 50 ms. Measure `len(json.dumps(forward))` per view: expect ≈ 70 KB. Write both numbers in the CHANGELOG entry. If over budget, lower the per-mark work before going on, not the budget.

### Task 5: Page — forward rows

**Files:**
- Modify: `webgui/pages/options/gamma_heat.py`, `webgui/pages/options/gamma.py` (`history_rows` neighbour)
- Test: `webgui/tests/test_gamma_heat.py` (or wherever `gamma_heat` is tested; grep `import gamma_heat`)

**Interfaces:**
- Produces:
  - `gamma_heat.forward_series(forward: dict | None, after_ts: float | None) -> {"rows": list, "up": list, "down": list}` — one row per minute, epoch ts from `floor(after_ts/60)*60 + 60` up to `forward["ts"][-1]` inclusive. Row: `(ts, spot, None, None, None, None, {float strike: {"call", "put", "net"}})`. Each side linearly interpolated in time between the marks around the minute (a minute before the first mark takes the first mark; `net = call + put`). `up`/`down`: the cone at each minute, same interpolation, or `None` where the cone is missing. `after_ts` None → start at the first mark. Junk-tolerant: bad input → empty lists.
  - `gamma.history_forward(payload, symbol) -> dict | None` — the payload's `forward` under the same symbol rule as `history_rows`.
- [ ] Tests: `test_minutes_between_marks_are_interpolated`, `test_rows_start_the_minute_after_the_last_collected`, `test_sides_interpolate_separately_and_net_is_their_sum`, `test_cone_follows_the_minutes`, `test_junk_forward_is_empty`, `test_history_forward_refuses_another_symbol`.
- [ ] Run, fail, implement, pass. Commit `feat(gamma): forward rows for the heatmap`.

### Task 6: `heatmap_figure` over the whole session

**Files:**
- Modify: `webgui/pages/options/gamma.py` (`heatmap_figure`, `heatmap_categories`; delete `projection_arg`, `heat_keeps_projection`)
- Test: `webgui/tests/test_options_gamma.py`

**Interfaces:**
- Consumes: `gamma_heat.forward_series`.
- Produces: `heatmap_figure(rows, view="GEX", ..., forward=None, ...)` (the `projection` keyword is replaced by `forward`, the raw dict). `heatmap_categories(rows, forward=None)` returns collected times + the forward rows' `HH:MM`.
- Inside: `n_obs = len(rows)`; `fwd = forward_series(forward, last collected ts)`; the matrix, change, frame, smoothing, share, `scale_max` and `contours` run on `rows + fwd["rows"]`. Spot line / candles / OHLC / level tracks use the first `n_obs` columns; the spot line continues flat at `forward["spot"]` over the forward columns; EM up/down from `fwd["up"]`/`fwd["down"]` at `n_obs + i`. The "now" divider (`className "gamma-now-divider"`) sits at `n_obs - 0.5` when there are forward rows. Both contour series always carry `"zoneAxis": "x"` and `"zones"`: `[{"value": n_obs - 0.5}, {"dashStyle": "ShortDash"}]` with forward rows, `[]` without. `xAxis.plotLines` is always emitted (`[]` without forward).
- [ ] Rewrite the old band tests: every `heat_keeps_projection` / `projection_arg` test is deleted with the functions (they pinned the dropping this design removes); `test_the_forward_band_is_not_smoothed` becomes `test_smoothing_runs_across_now` (the first forward column differs from its unsmoothed value and the last collected column is pulled toward it).
- [ ] New tests:
  - `test_every_view_and_mode_draws_the_band` (GEX/Charm/DEX/Vanna × net/call/put/size × level/open/window × strike/spot frame × adaptive/share: the heatmap data has points at x ≥ n_obs).
  - `test_contours_cross_now_and_are_dashed_past_it` (a contour point with x > n_obs - 0.5 exists; zones as above).
  - `test_zones_and_divider_are_cleared_without_forward` (Review Focus 2).
  - `test_change_window_at_a_projected_column_uses_real_minutes` (Review Focus 3: with 10 collected rows a minute apart and forward from minute 11, the window-5 change at column 14 is `value[14] - value[9]`).
  - `test_a_forward_only_strike_keeps_the_ladder_uniform` (Review Focus 5).
  - `test_series_count_is_fixed_with_and_without_forward`.
  - `test_categories_match_the_heatmap_columns` (len(`heatmap_categories`) == number of distinct x columns).
  - `test_scale_is_set_from_the_whole_session` (a large forward cell raises `zmax` above the collected-only figure under Adaptive; Locked keeps the lock).
- [ ] Run, fail, implement, run `webgui/tests/test_options_gamma.py`: PASS. Check `gamma.render`'s size ceiling test still passes. Commit `feat(gamma): heatmaps run to the close with projected contours`.

### Task 7: Page wiring, help and manuals

**Files:**
- Modify: `webgui/pages/options/gamma.py` (`_load_history` stores `state["fwd"][view] = history_forward(payload, sym)`, cleared with `state["hist"]`; the paint passes `forward=(state.get("fwd") or {}).get(view)` to `heatmap_figure` and `heatmap_categories`), `webgui/page_help.py` (`PROJECTION_HELP_MD` and the heatmap help), `docs/manuals/user-guide.md`, `docs/manuals/technical-reference.md` (grep "projection" / "15-min" / "forward"), `docs/webgui-routes.md` (the Gamma route), `docs/CHANGELOG.md`.
- [ ] Test: `test_the_paint_hands_the_view_its_own_forward` (source-level, like the existing paint wiring tests) and `test_page_help.py` still passes with the new wording (it must say: all four views, projected to the close, price / open interest / volatility held, dashed lines past now).
- [ ] Run `webgui` suite: `.venv\Scripts\python -m pytest webgui/tests -q -rf` (compare the failing SET with the baseline). Commit `docs(gamma): the projected afternoon in help and manuals`.

### Task 8: Verify in the page harness

- [ ] Start `tools/ui_harness.py` on a free loopback port with a fake bus carrying a GEX/Charm snapshot at 08:31, 12:00 and 14:58 (forward from Task 2's `build` on a synthetic chain). Check in the browser: the axis runs to 14:59, the "now" divider, dashed contours past it with Contours on, colours continuous at "now" with Smooth Medium, the hedge panel aligned, switching views / values / frames repaints without stray lines (repaint twice with the same data). Screenshot. Stop the harness.
- [ ] Run each touched suite once more, merge to `main`, push. Promote only outside 06:30–15:20 CT (the GEX collector rule), with the verbatim command, and leave it to the operator if it is a trading day.
