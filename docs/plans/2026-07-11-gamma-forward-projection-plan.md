# Gamma forward projection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show a forward gamma projection (flat-spot time-decay grid + expected-move cone) on the Gamma page's GEX heatmap out to the 4pm ET close, condense the header, reflect the projection in Explain/Analyze/scheduled briefings, and drop GEX collection from 2 min to 1 min.

**Architecture:** Additive across the existing 3 tiers — no new cache key / service / route. A pure Tier-2 projection re-prices today's standing OI at future times (reusing the engine's chain gamma × a BS time-decay ratio) and rides the existing `cache:options:gamma` GEX view; Tier-1 pure builders extend `heatmap_figure` and condense the header.

**Tech Stack:** Python 3.11, options_svc (FastAPI service), NiceGUI + Highcharts (webgui), gamma_tool/options_calculator engines, pytest.

**Design:** [2026-07-11-gamma-forward-projection-design.md](2026-07-11-gamma-forward-projection-design.md)

**Run tests from the repo root** with `.venv\Scripts\python -m pytest <path> -q` (services) or `cd webgui && ..\.venv\Scripts\python -m pytest <path> -q` (webgui), per the root CLAUDE.md.

---

### Task 1: Collection cadence 2 min → 1 min

Smallest, isolated change. The `test_scheduler.py` drift-guard is derived, so it stays green once all three constants move together.

**Files:**
- Modify: `options-scanner/gex_collector.py:28` (`POLL_INTERVAL_MIN`) + the `:59` comment
- Modify: `services/options_svc/scheduler.py:80` (`_GEX_INTERVAL_MIN`)
- Modify: `options-scanner/gex_status.py:10` (`STALE_AFTER_SEC`) + comment
- Test: `services/options_svc/tests/test_scheduler.py` (existing drift-guard, ~line 304)

**Step 1: Run the drift-guard now (baseline green at 2 min)**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_scheduler.py -q -k "interval or lockstep or drift"`
Expected: PASS.

**Step 2: Update the three constants**

- `gex_collector.py:28` → `POLL_INTERVAL_MIN = 1` (update the `:59` comment `== 240 at POLL_INTERVAL_MIN=2` → `== 120 at POLL_INTERVAL_MIN=1`).
- `scheduler.py:80` → `_GEX_INTERVAL_MIN = 1    # gex_collector.POLL_INTERVAL_MIN`.
- `gex_status.py:10` → `STALE_AFTER_SEC = 120  # 2 x poll interval (1 min)`.

**Step 3: Re-run the drift-guard + the full scheduler suite**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_scheduler.py -q`
Expected: PASS (the guard asserts `STALE_AFTER_SEC == POLL_INTERVAL_MIN*60*2` → `120 == 1*60*2`).

**Step 4: Commit**

```bash
git add options-scanner/gex_collector.py services/options_svc/scheduler.py options-scanner/gex_status.py
git commit -m "feat(gamma): collect GEX every 1 min (was 2 min)"
```

---

### Task 2: Pure projection math (`project_gex_grid` + `project_em_cone`)

The crux. Both live in `services/options_svc/compute.py` near `_session_expected_move`. **Contract-level ratio anchoring:** GEX uses the chain's provided gamma (`calc_all_from_chain` line ~984: `gamma * weight * 100 * spot*spot * 0.01`, call `+`, put `−`), so a future column scales each contract's *current* GEX contribution by the BS time-decay ratio `bs_gamma(S,K,T',σ)/bs_gamma(S,K,T_now,σ)` — the ratio is exactly 1.0 at `T'=T_now`, so the seam to the collected "now" column is continuous. Contracts with `σ<=0` (no vol) hold flat (ratio 1.0).

**Files:**
- Modify: `services/options_svc/compute.py` (add both functions + a small `_future_marks_ct` helper)
- Test: `services/options_svc/tests/test_projection.py` (create)

**Step 1: Write failing tests**

Create `services/options_svc/tests/test_projection.py`:

```python
"""Pure forward-projection math (compute.project_gex_grid / project_em_cone)."""
import datetime as dt
from zoneinfo import ZoneInfo

from services.options_svc import compute

CT = ZoneInfo("America/Chicago")


def _chain(spot=100.0, dte=0):
    """Minimal Schwab-shaped chain: one nearest expiry, a few strikes, call+put."""
    exp = f"2026-07-11:{dte}"
    def leg(strike, gamma, oi, iv=20.0):
        return [{"strike": strike, "gamma": gamma, "openInterest": oi,
                 "volatility": iv, "delta": 0.5, "daysToExpiration": dte}]
    strikes = {95.0: (0.03, 1000), 100.0: (0.06, 4000), 105.0: (0.03, 1500)}
    call_map = {exp: {f"{k:.1f}": leg(k, g, oi) for k, (g, oi) in strikes.items()}}
    put_map = {exp: {f"{k:.1f}": leg(k, g, oi) for k, (g, oi) in strikes.items()}}
    return {"underlyingPrice": spot, "callExpDateMap": call_map, "putExpDateMap": put_map}


def test_future_marks_to_close():
    now = dt.datetime(2026, 7, 11, 13, 5, tzinfo=CT)   # 1:05pm CT
    marks = compute._future_marks_ct(now)
    assert marks[0].strftime("%H:%M") == "13:15"       # next quarter hour
    assert marks[-1].strftime("%H:%M") == "15:00"      # the close
    assert all(m.minute % 15 == 0 for m in marks)


def test_future_marks_empty_after_close():
    now = dt.datetime(2026, 7, 11, 15, 30, tzinfo=CT)
    assert compute._future_marks_ct(now) == []


def test_project_grid_shape_and_seam():
    import gamma_tool as gt
    eng = gt.GammaEngine()
    chain = _chain(dte=0)
    now = dt.datetime(2026, 7, 11, 13, 0, tzinfo=CT)
    proj = compute.project_gex_grid(eng, chain, 100.0, now)
    assert proj["times"] and len(proj["times"]) == len(next(iter(proj["grid"].values())))
    assert proj["spot"] == 100.0
    # 0-DTE net gamma concentrates toward ATM into the close: the ATM strike's
    # |net| at the LAST future mark >= its |net| at the FIRST (time decay sharpens).
    atm = proj["grid"]["100.0"]
    assert abs(atm[-1]) >= abs(atm[0])


def test_project_grid_empty_after_close():
    import gamma_tool as gt
    now = dt.datetime(2026, 7, 11, 15, 30, tzinfo=CT)
    proj = compute.project_gex_grid(gt.GammaEngine(), _chain(), 100.0, now)
    assert proj["times"] == [] and proj["grid"] == {}


def test_project_grid_defensive_on_bad_chain():
    import gamma_tool as gt
    now = dt.datetime(2026, 7, 11, 13, 0, tzinfo=CT)
    assert compute.project_gex_grid(gt.GammaEngine(), {}, 0, now)["grid"] == {}


def test_em_cone_sqrt_time_fan():
    now = dt.datetime(2026, 7, 11, 13, 0, tzinfo=CT)
    marks = compute._future_marks_ct(now)
    cone = compute.project_em_cone(100.0, 0.20, marks, now)
    assert len(cone["mid"]) == len(marks)
    assert all(m == 100.0 for m in cone["mid"])          # flat spot midline
    # widens with sqrt(time): the last band half-width > the first
    assert (cone["up"][-1] - 100.0) > (cone["up"][0] - 100.0)
    assert cone["down"][0] < 100.0 < cone["up"][0]
```

**Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_projection.py -q`
Expected: FAIL (`_future_marks_ct` / `project_gex_grid` / `project_em_cone` not defined).

**Step 3: Implement in `services/options_svc/compute.py`**

Add near `_session_expected_move` (import `bs_gamma` lazily to keep options-scanner off the module import path until needed):

```python
def _future_marks_ct(now):
    """15-min CT marks from the next quarter-hour through 15:00 CT (the close).

    Returns [] once ``now`` is at/after the close (off-hours hides the band)."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    now = now.astimezone(ct)
    close = now.replace(hour=15, minute=0, second=0, microsecond=0)
    if now >= close:
        return []
    # round UP to the next quarter hour
    q = (now.minute // 15 + 1) * 15
    mark = now.replace(minute=0, second=0, microsecond=0) + _dt.timedelta(minutes=q)
    out = []
    while mark <= close:
        out.append(mark)
        mark = mark + _dt.timedelta(minutes=15)
    return out


def _T_at(dte, mark_ct):
    """Engine-consistent time-to-expiry (years) at a CT wall-clock ``mark_ct``.

    Mirrors GammaEngine.calc_all_from_chain: T = (dte*24 + hours_to_close)/(365*24),
    floored at 1e-6, hours measured to 15:00 CT (the 4pm ET cash close)."""
    hours_left = max(0.0, (15 - mark_ct.hour) + (0 - mark_ct.minute) / 60.0)
    return max((dte * 24 + hours_left) / (365 * 24), 1e-6)


def project_gex_grid(eng, chain, spot, now):
    """Flat-spot time-decay projection of net GEX per strike to the 4pm ET close.

    Re-prices today's standing OI at future 15-min marks with spot held flat: each
    contract's CURRENT GEX contribution (chain gamma) is scaled by the BS gamma
    time-decay ratio bs_gamma(S,K,T',sigma)/bs_gamma(S,K,T_now,sigma) — 1.0 at
    T'=T_now so the seam to the collected 'now' column is continuous; sigma<=0 holds
    flat. Pure + defensive: {} grid on any failure. Returns
    {"times":[HH:MM...], "grid":{strike_str:[net_t0...]}, "spot": spot}."""
    import datetime as _dt
    empty = {"times": [], "grid": {}, "spot": spot}
    try:
        if not chain or not spot or spot <= 0:
            return empty
        marks = _future_marks_ct(now)
        if not marks:
            return empty
        from options_calculator import bs_gamma
        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        today = now.astimezone(_dt.timezone.utc).astimezone(
            __import__("zoneinfo").ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
        ck, cdte = eng._find_nearest_exp_key(call_map, today)
        pk, pdte = eng._find_nearest_exp_key(put_map, today)
        dtes = [d for d in (cdte, pdte) if d is not None]
        dte = min(dtes) if dtes else 0
        r = 0.045
        t_now = _T_at(dte, now.astimezone(
            __import__("zoneinfo").ZoneInfo("America/Chicago")))
        t_future = [_T_at(dte, m) for m in marks]
        grid: dict[str, list[float]] = {}

        def _accumulate(exp_key, exp_map, sign):
            if not exp_key:
                return
            for strike_str, contracts in exp_map.get(exp_key, {}).items():
                strike = float(strike_str)
                key = str(strike)
                for c in contracts:
                    oi = c.get("openInterest", 0) or 0
                    gamma = c.get("gamma", 0) or 0
                    if oi <= 0 or gamma == 0:
                        continue
                    base = sign * gamma * oi * 100 * spot * spot * 0.01
                    iv = (c.get("volatility", 0) or 0) / 100.0
                    denom = bs_gamma(spot, strike, t_now, r, iv) if iv > 0 else 0.0
                    row = grid.setdefault(key, [0.0] * len(marks))
                    for i, tf in enumerate(t_future):
                        if iv > 0 and denom > 0:
                            ratio = bs_gamma(spot, strike, tf, r, iv) / denom
                        else:
                            ratio = 1.0
                        row[i] += base * ratio

        _accumulate(ck, call_map, +1.0)
        _accumulate(pk, put_map, -1.0)
        return {"times": [m.strftime("%H:%M") for m in marks], "grid": grid, "spot": spot}
    except Exception:
        log.debug("project_gex_grid failed", exc_info=True)
        return empty


def project_em_cone(spot, atm_iv, marks, now):
    """Up/mid/down expected-move fan over the future marks (flat-spot midline).

    half_width(tau) = spot*atm_iv*sqrt(tau/365), tau = calendar days from ``now`` to
    the mark. Returns {"mid":[...], "up":[...], "down":[...]} (empty lists if inputs
    are unusable)."""
    import math
    out = {"mid": [], "up": [], "down": []}
    try:
        if not spot or spot <= 0 or not atm_iv or atm_iv <= 0 or not marks:
            return out
        for m in marks:
            tau_days = max(0.0, (m - now).total_seconds() / 86400.0)
            hw = spot * atm_iv * math.sqrt(tau_days / 365.0)
            out["mid"].append(spot)
            out["up"].append(spot + hw)
            out["down"].append(spot - hw)
        return out
    except Exception:
        log.debug("project_em_cone failed", exc_info=True)
        return {"mid": [], "up": [], "down": []}
```

**Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_projection.py -q`
Expected: PASS. (If `test_project_grid_shape_and_seam`'s ATM-sharpening assertion is flaky for the toy chain, keep it — a 0-DTE ATM strike's BS gamma rises as T→0, so `abs(atm[-1]) >= abs(atm[0])` holds.)

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_projection.py
git commit -m "feat(gamma): pure forward GEX projection + EM cone (Tier-2 math)"
```

---

### Task 3: Embed the projection in `gamma_snapshot` (GEX view only)

**Files:**
- Modify: `services/options_svc/compute.py` `gamma_snapshot` (the GEX view entry, ~line 1227) — it already holds `chain`, `eng`, `spot`, and computes `now`.
- Test: `services/options_svc/tests/test_compute.py` (add a projection-embed test)

**Step 1: Write the failing test** — assert the GEX view carries a `projection` block with `times`/`grid`/`cone`/`spot` when RTH, and that OTHER views don't. Mock/monkeypatch `_gamma_fetch_chain` to return a toy chain (reuse `test_projection._chain`) and monkeypatch `_sched._market_now` to a 1pm CT trading time; assert `snap["views"]["GEX"]["projection"]["grid"]` is non-empty and `"projection" not in snap["views"]["Charm"]`.

**Step 2: Run → FAIL** (`KeyError: 'projection'`).

**Step 3: Implement.** In `gamma_snapshot`, after the GEX `entry` is built (inside the `for vname` loop, guard `if vname == "GEX"`), compute and attach — cropping to the same display window used by `_crop_gamma_views` (call `project_gex_grid` on the FULL grid first, then crop its `grid` keys to the near-spot window with the existing helper / same `GAMMA_N_SIDE`):

```python
if vname == "GEX":
    try:
        proj = project_gex_grid(eng, chain, spot, now)
        atm_iv = _atm_iv_from_chain(chain)   # reuse the Analyze EM ATM-IV helper
        marks_dt = _future_marks_ct(now)
        proj["cone"] = project_em_cone(spot, atm_iv, marks_dt, now)
        entry["projection"] = proj
    except Exception:
        log.debug("gamma projection attach failed", exc_info=True)
```

Then extend `_crop_gamma_views` (or crop inline right here) so `entry["projection"]["grid"]` keeps only strikes within the ±display window — same crop the collected grid gets, so the payload stays <1 MB. If `_session_expected_move` already derives ATM IV, expose that intermediate as `_atm_iv_from_chain` (small refactor) rather than duplicating.

**Step 4: Run → PASS.**

**Step 5: Commit** `feat(gamma): embed forward projection in the GEX snapshot view`.

---

### Task 4: Heatmap UI — future columns + cone + 40/60 split

**Files:**
- Modify: `webgui/pages/options/gamma.py` — `heatmap_figure` (add `projection=None` arg), a new `_STRIKE_HEAT_SPLIT` constant, `_apply_flex`/`_render_view` wiring, `_refloat_keys` handling for the projection grid.
- Test: `webgui/tests/test_gamma.py` (extend)

**Step 1: Write failing tests**

```python
def test_heatmap_appends_projection_columns():
    rows = _sample_rows()                       # existing collected-rows helper
    proj = {"times": ["13:15", "13:30"], "spot": 100.0,
            "grid": {"100.0": [50000.0, 80000.0], "105.0": [10000.0, 12000.0]},
            "cone": {"mid": [100.0, 100.0], "up": [100.5, 100.8], "down": [99.5, 99.2]}}
    fig = gamma.heatmap_figure(rows, view="GEX", yrange=(90, 110), projection=proj)
    cats = fig["xAxis"]["categories"]
    assert cats[-2:] == ["13:15", "13:30"]                       # future cols appended
    assert any(pl.get("className") == "gamma-now-divider" or pl.get("value") is not None
               for pl in fig["xAxis"].get("plotLines", []))       # a 'now' divider exists
    names = [s.get("name") for s in fig["series"]]
    assert "EM up" in names and "EM down" in names                # cone overlays present


def test_heatmap_no_projection_unchanged():
    rows = _sample_rows()
    fig = gamma.heatmap_figure(rows, view="GEX", yrange=(90, 110), projection=None)
    assert "gamma-now-divider" not in str(fig["xAxis"].get("plotLines", []))


def test_strike_heat_split_constant():
    assert gamma._STRIKE_HEAT_SPLIT == (0.40, 0.60)              # flip to (0.70,0.30) if hard to read
```

**Step 2: Run → FAIL.**

**Step 3: Implement.**
- Add `_STRIKE_HEAT_SPLIT = (0.40, 0.60)  # (strike, heat); flip to (0.70, 0.30) if the day gets hard to read`.
- In `_apply_flex` (non-Term branch) replace `bar_w, heat_w = panel_flex(n_cols)` with `bar_w, heat_w = _STRIKE_HEAT_SPLIT`.
- In `heatmap_figure(rows, view, height, yrange, projection=None)`: after building the collected `times`/`data`, if `projection` and its `times`:
  - append `projection["times"]` to the `categories` list; let `base = len(collected_times)`.
  - emit future heatmap points `[base + j, float(strike), net]` for each strike in `projection["grid"]` within `yrange`, on the SAME heatmap series/colorAxis (so they color on the same scale).
  - add an xAxis plotLine at `base - 0.5` (`className: "gamma-now-divider"`, dashed, faint) — the seam.
  - continue the Spot line into the future along `cone["mid"]` (points `[base + j, mid_j]`), and add two faint dashed **line** series `"EM up"` / `"EM down"` (`colorAxis: False`) over `[base + j, up_j]` / `[base + j, down_j]`.
- In `_render_view`, read `snap["views"]["GEX"].get("projection")`, `_refloat_keys` its `grid`, and pass it to `heatmap_figure` ONLY when `view == "GEX"` (else `projection=None`).

**Step 4: Run → PASS** (`cd webgui && ..\.venv\Scripts\python -m pytest tests\test_gamma.py -q`).

**Step 5: Commit** `feat(gamma): forward projection band + EM cone on the GEX heatmap; fixed 40/60 split`.

---

### Task 5: Analyze + scheduled briefings — "into the close" outlook

**Files:**
- Modify: `services/options_svc/compute.py` — `_ANALYZE_TOOL` schema (add `close_outlook`), `_gamma_blocks_for` / the prompt bundler to include a per-index projection summary, `_parse_analysis` to carry `close_outlook`, and `analyze_infographic_html` / `_index_card_html` to render it.
- Test: `services/options_svc/tests/test_compute.py` (analyze tests)

**Step 1: Write failing tests** — (a) the `submit_analysis` schema exposes an optional `close_outlook` per index; (b) `_parse_analysis` round-trips a `close_outlook` string onto each index; (c) `analyze_infographic_html` renders the `close_outlook` text when present and omits it cleanly when absent; (d) a new `compute._projection_brief(eng, chain, spot, now)` returns a compact string with projected close flip/walls + EM-to-close range.

**Step 2: Run → FAIL.**

**Step 3: Implement.**
- Add `_projection_brief(...)`: run `project_gex_grid` + `project_em_cone`, take the LAST future column, derive projected flip (net-GEX zero-cross) + call/put walls (argmax/argmin) via the existing `gamma_walls`-style logic on the projected column, and the close EM band; return one reader-first line ("Into the close: flip drifts to X, call wall firms at Y, put wall at Z; EM band A–B.").
- Thread the per-index brief into the prompt bundle in `gamma_analyze` (it already loops $SPX/SPY/QQQ) so the model can echo/refine it.
- Add `close_outlook` (optional string) to each index item in `_ANALYZE_TOOL["input_schema"]...["items"]["properties"]`, with a reader-first description ("One line on what to do as the day decays into the close").
- `_parse_analysis`: copy `close_outlook` onto each parsed index (defensive default `""`).
- `_index_card_html`: render a small "Into the close" line when `close_outlook` is non-empty.

**Step 4: Run → PASS** (`.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -q -k "analyze or projection or outlook"`).

**Step 5: Commit** `feat(gamma): Analyze + scheduled briefings carry an into-the-close outlook`.

---

### Task 6: Explain — forward "Into the close" block (GEX)

**Files:**
- Modify: `options-scanner/gamma_infographic.py` (`_derive` + `_render_terminal`) to add a GEX-only forward section fed by a projection summary; the summary is computed in `services/options_svc/compute.py` (where the chain lives) and passed into the Explain builder call.
- Test: `options-scanner/tests/test_gamma_infographic.py` (or the webgui explain test, wherever Explain is currently tested)

**Step 1: Write the failing test** — for the GEX view with a projection summary, `_render_terminal` output contains an "Into the close" heading + the projected levels; for Charm/DEX/Vanna (or when the summary is None) it is absent.

**Step 2: Run → FAIL.**

**Step 3: Implement** — reuse `_projection_brief` (Task 5) to produce the text; add a `projection=None` kwarg to the Explain builder and render the block only for GEX when provided (reader-first phrasing, no dealer mechanics). Keep it a pure string section (Explain is a `ui.html` fragment — out of scope for Tailwind, styled via the existing `EXPLAIN_CSS`).

**Step 4: Run → PASS.**

**Step 5: Commit** `feat(gamma): Explain shows an into-the-close forward read (GEX)`.

---

### Task 7: Condense the header (4 rows → 2)

**Files:**
- Modify: `webgui/pages/options/gamma.py` `render()` (lines ~765–803) + the `_sync_sched_btns` logic that highlights the auto-briefing buttons.
- Test: `webgui/tests/test_gamma.py` (pure-builder tests only; layout verified in the browser)

**Step 1:** Extract a pure `status_strip_text(gex_status, summary, countdown)` builder that composes the one-line `·`-separated status strip (collector · last/next scan · next refresh · spot/strikes/net/flip) and unit-test it.

**Step 2: Run → FAIL** (function missing).

**Step 3: Implement.**
- **Row 1:** keep Symbol · view toggle · Refresh · Explain · Analyze; replace the full "Auto briefings" row with a single `ui.button("Briefings", icon="schedule")` opening a `ui.menu` whose four items are the slots (carry the today/dim highlight onto the menu items via `_sync_sched_btns`).
- **Row 2:** one `ui.row` with a single `status_lbl` bound to `status_strip_text(...)` (fold collector status + last/next scan + countdown + summary). Remove the separate `last_scan_lbl`/`next_scan_lbl`/`summary_lbl` rows; fold the DEX 0-DTE `pressure_box` inline into this strip on the DEX view.
- Update `_sync_sched_btns`, the status/summary repaint calls, and the countdown timer to target the new single label / menu items.

**Step 4: Run → PASS** (`cd webgui && ..\.venv\Scripts\python -m pytest tests\test_gamma.py -q`).

**Step 5: Browser-verify** — start the `webgui` preview, open `/options/gamma`, confirm 2 header rows + the taller heatmap + the Briefings menu. Screenshot.

**Step 6: Commit** `feat(gamma): condense header to two rows for more chart real estate`.

---

### Task 8: Docs + live verification

**Step 1:** Restart `options_svc` + the webgui; on `/options/gamma` (RTH) confirm the GEX heatmap shows the future band right of the "now" divider with the EM cone, the 40/60 split, 1-min collection (Last scan cadence), a fresh Analyze/Explain "into the close" read. Off-hours: confirm NO future band. (If off-hours, verify via a Redis read of `cache:options:gamma` → `views.GEX.projection` is empty, and via the projection unit tests.)

**Step 2:** Update the root `CLAUDE.md` (Gamma route row + a dated "Last updated" entry) and `options-scanner/CLAUDE.md` (the 2-min→1-min poll note; the forward-projection mention). Keep additions terse.

**Step 3: Commit** `docs(gamma): record forward projection + 1-min collection + header condense`.

**Step 4:** Run the full affected suites green before wrapping:
`.venv\Scripts\python -m pytest services\options_svc -q` and `cd webgui && ..\.venv\Scripts\python -m pytest -q`.

---

## Notes for the implementer

- **DRY:** reuse `eng._find_nearest_exp_key`, the engine's exact GEX formula (chain gamma × OI × 100 × S² × 0.01), `_session_expected_move`'s ATM-IV derivation, `gamma_walls` for projected levels, and the existing `_crop_gamma_views` window. Do NOT reimplement GEX.
- **YAGNI:** GEX only; no IV shock; no Charm/DEX/Vanna projection; no off-hours band.
- **Defensive:** every projection path returns empty on failure so the page degrades to collected-only — never a 500.
- **Tier rule:** the webgui imports only `nicegui` + `shared.bus` + `shared.contracts`; all engine/chain work stays in options_svc.
