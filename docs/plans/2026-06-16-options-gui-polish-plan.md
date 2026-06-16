# Options GUI Polish Batch — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a batch of Options-section UI/UX fixes plus two small `options_svc` additions (Gamma collector status), excluding streaming-driven repricing.

**Architecture:** 3-tier. UI changes live in `webgui/pages/options/*.py` + `webgui/main.py` (pure GUI, read from `bus_client`). Two items add a published view from `services/options_svc/` (gex status) and one tweaks a server-side compute (calculator range). TDD the pure functions; smoke-verify rendering via the NiceGUI preview on :8500.

**Tech Stack:** NiceGUI (Quasar `ui.table` slots, `ui.expansion`, `ui.input`), Plotly dicts, pytest, Redis bus (`shared.bus` / fakeredis under test).

**Design:** [2026-06-16-options-gui-polish-design.md](2026-06-16-options-gui-polish-design.md)

**Test commands:**
- webgui: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
- options_svc: `..\.venv\Scripts\python -m pytest services/options_svc/tests -q` (from repo root)

**Conventions:** Keep `render()` thin; pure transforms are module-level + unit-tested. `ui.table` per-cell coloring uses a `body-cell-<field>` slot (`add_slot`) with a Quasar `<q-td>` + `<q-badge>`/`<q-chip>`; store any computed color in the row dict. Commit after each task with a conventional prefix.

---

## Task 1: Persistent nav dropdowns (`webgui/main.py`)

**Files:**
- Modify: `webgui/main.py` (the `_layout` contextmanager + module top)

**Step 1 — Read** `webgui/main.py` around `_layout` (the `ui.expansion("Options"...)` / `ui.expansion("Sentiment"...)` block) to get exact lines.

**Step 2 — Add module-level state** near the other module globals:
```python
# Persisted left-nav expansion state (single-user). None = "use active default".
_NAV_OPEN: dict[str, bool] = {}
```

**Step 3 — Replace each expansion** so it reads/writes `_NAV_OPEN`:
```python
opt_default = active == "/" or active.startswith("/options")
opt = ui.expansion("Options", icon="candlestick_chart",
                   value=_NAV_OPEN.get("Options", opt_default)).classes("w-full")
opt.on_value_change(lambda e: _NAV_OPEN.__setitem__("Options", e.value))
with opt:
    for path, label, icon in OPTIONS_CHILDREN:
        _nav_link(path, label, icon, active)
```
Repeat for "Sentiment" (`value=_NAV_OPEN.get("Sentiment", sentiment_active)`).

**Step 4 — Verify (preview):** start `webgui`, expand Options, collapse Sentiment, navigate between pages → state persists. Toggle and confirm it sticks.

**Step 5 — Commit:** `feat(webgui): persist left-nav dropdown open/closed state across navigation`

---

## Task 2: Scanner — color signals by quality (`webgui/pages/options/scanner.py`)

**Files:**
- Modify: `webgui/pages/options/scanner.py`
- Test: `webgui/tests/test_options_scanner.py` (create if absent)

**Step 1 — Failing test** for the pure color function:
```python
from pages.options import scanner
def test_score_zone_color():
    assert scanner.score_zone_color(30) == scanner.RED
    assert scanner.score_zone_color(50) == scanner.AMBER
    assert scanner.score_zone_color(60) == scanner.BLUE
    assert scanner.score_zone_color(90) == scanner.GREEN
    assert scanner.score_zone_color(None) == "#666666"
```
Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_options_scanner.py -q` → FAIL.

**Step 2 — Implement** in scanner.py (reuse zone thresholds from `svg.py`/`detail.py` colors):
```python
RED, AMBER, BLUE, GREEN = "#ef5350", "#ffa726", "#42a5f5", "#66bb6a"
def score_zone_color(score):
    if score is None: return "#666666"
    if score < 40: return RED
    if score < 55: return AMBER
    if score < 75: return BLUE
    return GREEN
```

**Step 3 — Wire the table slot.** In `_populate`/row build, add `row["_score_color"] = score_zone_color(row["composite_score"])`. After creating the `ui.table`, add:
```python
table.add_slot('body-cell-composite_score', r'''
  <q-td :props="props">
    <q-badge :style="`background:${props.row._score_color};color:#111`" :label="props.value ?? '—'"/>
  </q-td>
''')
```

**Step 4 — Tests pass + preview** shows colored score chips. Run pytest.

**Step 5 — Commit:** `feat(options/scanner): color signals by quality score`

---

## Task 3: Scanner — plain-English term text

**Files:** Modify `webgui/pages/options/scanner.py`; Test `webgui/tests/test_options_scanner.py`.

**Step 1 — Failing test:**
```python
def test_term_text_contango():
    out = scanner.term_text({"structure": "CONTANGO"}, "2026-06-15T13:32:56-05:00")
    assert "Contango" in out and "1:32" in out and "as of" in out
def test_term_text_unknown_blank():
    assert scanner.term_text({}, None) == ""
```

**Step 2 — Implement:**
```python
_TERM_PHRASES = {
    "CONTANGO": "Contango (near-term calm)",
    "BACKWARDATION": "Backwardation (near-term stress)",
    "MIXED": "Mixed term structure",
}
def term_text(term, ts):
    structure = (term or {}).get("structure")
    if not structure or structure == "UNKNOWN":
        return ""
    phrase = _TERM_PHRASES.get(structure, structure.title())
    when = _short_time(ts)  # parse ISO -> "%-I:%M %p" (use %#I on Windows / strip leading zero)
    tail = f" · as of {when}" if when else ""
    return f"VIX term: {phrase}{tail}"
```
Add `_short_time(iso)` helper: parse with `datetime.fromisoformat`, format `"%I:%M %p"` and lstrip("0"); return "" on parse failure.

**Step 3 — Replace** the `_scan_meta_strip` body that builds `f"Term: {term['structure']}"` and `f"as of {ts}"` with a single `ui.label(term_text(results.get("vix_term_structure"), results.get("timestamp")))` (skip if empty).

**Step 4 — pytest + preview.**

**Step 5 — Commit:** `feat(options/scanner): plain-English VIX term-structure label`

---

## Task 4: Scanner — highlight new signals (session diff)

**Files:** Modify `webgui/pages/options/scanner.py`; Test `webgui/tests/test_options_scanner.py`.

**Step 1 — Failing test:**
```python
def test_mark_new_flags_unseen_keys():
    rows = [{"symbol":"SPY","type":"PCS","short_strike":1,"long_strike":2,"expiration":"x"}]
    keys, marked = scanner.mark_new(rows, set())
    assert marked[0]["_new"] is False        # first load: nothing is "new"
    keys2, marked2 = scanner.mark_new(rows + [{"symbol":"QQQ","type":"CCS","short_strike":3,"long_strike":4,"expiration":"y"}], keys)
    assert marked2[0]["_new"] is False and marked2[1]["_new"] is True
```

**Step 2 — Implement:**
```python
def _sig_key(r):
    return f'{r.get("symbol")}|{r.get("type")}|{r.get("short_strike")}|{r.get("long_strike")}|{r.get("expiration")}'
def mark_new(rows, prev_keys):
    keys = {_sig_key(r) for r in rows}
    first = not prev_keys
    for r in rows:
        r["_new"] = (not first) and _sig_key(r) not in prev_keys
    return keys, rows
```

**Step 3 — Wire:** keep `state["seen_keys"] = set()`; in `_populate`, after building rows call `state["seen_keys"], rows = mark_new(rows, state["seen_keys"])`. Add a `body-cell` slot (e.g. on `symbol` or a new leading column) rendering a `<q-badge color="primary" label="NEW"/>` when `props.row._new`, and/or set a row tint via `:class`. Apply to both 0-DTE and swing tables.

**Step 4 — pytest + preview** (trigger a rescan and confirm new rows are badged).

**Step 5 — Commit:** `feat(options/scanner): highlight newly-appeared signals since last scan`

---

## Task 5: Paper Trades — fix detail panel not updating

**Files:** Modify `webgui/pages/options/paper.py`.

**Step 1 — Read** `_populate`, `_select`, and the `detail_panel`/`raw_by_id`/`table` setup.

**Step 2 — Track selection + re-render.** In `_populate`, before `raw_by_id.clear()`:
```python
sel = table.selected[0].get("id") if table.selected else None
```
After repaint:
```python
if sel and sel in raw_by_id:
    detail_panel.update(synth_from_trade(raw_by_id[sel]))
elif sel:
    detail_panel.clear()
```
(If `table.selected` isn't reliably populated by row click, also stash the selected id in page state inside `_select`.)

**Step 3 — Verify (preview):** select a trade, trigger a data refresh (e.g. reprice/manage), confirm the detail panel updates with fresh values instead of going stale.

**Step 4 — Commit:** `fix(options/paper): keep trade-detail panel in sync on data refresh`

---

## Task 6: Captured — drift as x.xx

**Files:** Modify `webgui/pages/options/captured.py`; Test `webgui/tests/test_options_captured.py` (create if absent).

**Step 1 — Failing test:**
```python
from pages.options import captured
def test_captured_rows_rounds_drift():
    rows = captured.captured_rows([{"score_drift": -4.0}])
    assert rows[0]["score_drift"] == -4.00
    rows2 = captured.captured_rows([{"score_drift": 1.23456}])
    assert rows2[0]["score_drift"] == 1.23
    assert captured.captured_rows([{}])[0]["score_drift"] is None
```

**Step 2 — Implement:** in `captured_rows`, change the drift line to round: `"score_drift": (round(v, 2) if (v := s.get("score_drift")) is not None else None),` (or a `_round2` helper).

**Step 3 — pytest + preview.**

**Step 4 — Commit:** `feat(options/captured): show score drift rounded to 2 decimals`

---

## Task 7: Captured — color by recommendation

**Files:** Modify `webgui/pages/options/captured.py`; Test `webgui/tests/test_options_captured.py`.

**Step 1 — Failing test:**
```python
def test_rec_color():
    assert captured.rec_color("TAKE_PROFIT") == captured.GREEN
    assert captured.rec_color("CUT") == captured.RED
    assert captured.rec_color("HOLD") == captured.AMBER
    assert captured.rec_color("?") == "#666666"
```

**Step 2 — Implement:**
```python
RED, AMBER, GREEN = "#ef5350", "#ffa726", "#66bb6a"
def rec_color(rec):
    return {"TAKE_PROFIT": GREEN, "CUT": RED, "HOLD": AMBER}.get(rec, "#666666")
```
Set `row["_rec_color"] = rec_color(row["recommendation"])` in `captured_rows`.

**Step 3 — Wire slot** on the table:
```python
table.add_slot('body-cell-recommendation', r'''
  <q-td :props="props">
    <q-badge :style="`background:${props.row._rec_color};color:#111`" :label="props.value"/>
  </q-td>
''')
```

**Step 4 — pytest + preview.**

**Step 5 — Commit:** `feat(options/captured): color rows by HOLD/TAKE_PROFIT/CUT recommendation`

---

## Task 8: Calculator — symmetric P&L matrix about spot

**Files:** Modify `services/options_svc/compute.py` (`calc_compute`); Test `services/options_svc/tests/test_compute.py`.

**Step 1 — Read** `compute.calc_compute` to see how `price_range`/strikes are passed to `options_calculator.calc_spread_pnl`.

**Step 2 — Failing test** for a pure helper:
```python
def test_symmetric_price_range_centers_spot_and_includes_strikes():
    lo, hi = compute.symmetric_price_range(100.0, [92.0, 108.0], pct=0.05)
    assert round((lo+hi)/2, 6) == 100.0        # symmetric about spot
    assert lo <= 92.0 and hi >= 108.0          # strikes in view
def test_symmetric_price_range_default_band_when_strikes_inside():
    lo, hi = compute.symmetric_price_range(100.0, [99.0, 101.0], pct=0.05)
    assert (lo, hi) == (95.0, 105.0)
```

**Step 3 — Implement:**
```python
def symmetric_price_range(spot, strikes, pct=0.05):
    half = spot * pct
    for k in strikes or []:
        half = max(half, abs(k - spot))
    return (round(spot - half, 2), round(spot + half, 2))
```
Use it in `calc_compute` to build the range passed to the engine (replacing/feeding `generate_price_range`), with the params' short/long strikes. Confirm the engine's row stepping keeps spot centered (equal rows each side); if its floor/ceil clamps asymmetrically, pass the symmetric `(lo, hi)` directly so the midpoint is spot.

**Step 4 — pytest** (`services/options_svc/tests`) + **preview** Calculator: matrix symmetric about spot, strikes visible.

**Step 5 — Commit:** `feat(options/calculator): symmetric P&L grid about spot incl. strikes`

---

## Task 9: Symbol inputs auto-select on focus

**Files:** Modify `webgui/pages/options/{calculator,gamma,simulator,swing}.py` (and `scanner.py` if it has a symbol input).

**Step 1 — Locate** each `symbol_in = ui.input("Symbol", ...)`.

**Step 2 — Add** a shared helper (e.g. in a small util or inline per page):
```python
def _select_all_on_focus(inp):
    inp.on('focus', js_handler='(e) => { const i = e.target.closest(".q-field")?.querySelector("input"); (i||e.target).select(); }')
    return inp
```
Apply: `_select_all_on_focus(symbol_in)` after each creation.

**Step 3 — Verify (preview):** click/tab into each Symbol field → existing text is highlighted; typing replaces it. (Adjust the JS selector if Quasar nesting differs — confirm by observing in preview.)

**Step 4 — Commit:** `feat(options): auto-select Symbol field text on focus`

---

## Task 10: Gamma status bar (service view + page bar)

### 10a — Service view

**Files:** Modify `services/options_svc/compute.py`, `handlers.py`, `scheduler.py`; Test `services/options_svc/tests/test_compute.py` + `test_handlers.py`.

**Step 1 — Failing test** for `compute.gex_status_view` (fake `gex_status`/`gex_history_db` + a fixed now):
```python
def test_gex_status_view_shape(monkeypatch):
    # fake gex_history_db.last_snapshot_age -> (120, <ts>), gex_status.classify -> ("✓ ...","green")
    out = compute.gex_status_view()
    assert set(out) >= {"status_label","status_color","last_scan","next_scan","age_seconds"}
```

**Step 2 — Implement** `compute.gex_status_view()`: lazy-import `gex_status`, `gex_history_db`; open read-only conn; `age, last_ts = last_snapshot_age(conn, "$SPX", "gex")`; `label, color = gex_status.classify_collector_status(age, now_ct, has_data, last_ts)`; compute `next_scan` = next 5-min boundary within 08:30–15:20 CT (reuse scheduler window constants) or `None` outside hours; format `last_scan`/`next_scan` to short local time. Return the dict. (No exception escapes — defensive defaults.)

**Step 3 — Implement** `handlers.publish_gex_status(bus)`:
```python
CACHE_GEX_STATUS = "cache:options:gex_status"; EVENT_GEX_STATUS = "events:options:gex_status"
def publish_gex_status(bus):
    data = compute.gex_status_view()
    version = bus.cache_set(CACHE_GEX_STATUS, data)
    bus.publish(EVENT_GEX_STATUS, {"version": version})
```
Test: with `Bus(fake=True)`, monkeypatch `handlers.compute.gex_status_view` → publishes + caches.

**Step 4 — Wire scheduler:** in `scheduler.loop`'s 30s tick, add a guarded `await loop_.run_in_executor(None, handlers.publish_gex_status, bus)` (independent try/except, like the header refresh) + a one-shot at startup.

**Step 5 — pytest** options_svc.

**Step 6 — Commit:** `feat(options_svc): publish GEX collector status (last/next scan) view`

### 10b — Page bar

**Files:** Modify `webgui/pages/options/gamma.py`.

**Step 7 — Add a status bar** row near the controls: labels for collector status (colored dot/text), last scan, next scan. Version-poll `options:gex_status` via the existing timer pattern (`bus_client.read_version`/`read`), repaint labels. Keep the existing "Next refresh" countdown alongside.

**Step 8 — Verify (preview):** status bar shows `Collector: ✓ … · Last scan … · Next scan …` and updates.

**Step 9 — Commit:** `feat(options/gamma): collector status bar (status + last/next scan)`

---

## Wrap-up

**Step — Full suites green:**
- `cd webgui && ..\.venv\Scripts\python -m pytest -q`
- `..\.venv\Scripts\python -m pytest services/options_svc/tests -q`

**Step — Update** root `CLAUDE.md` Options/Gamma notes (status bar; scanner coloring/term text/new-signal marking; captured drift/coloring; nav persistence) per the standing maintenance requirement.

**Step — Final preview smoke** of each touched page.
