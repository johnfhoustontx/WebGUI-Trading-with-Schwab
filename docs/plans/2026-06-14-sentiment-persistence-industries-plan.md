# Sentiment — persistence + industry expansion — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `/sentiment` persist across navigation (instant cached repaint, no re-fetch) and make each sector row expand into its industry sub-rows (lazy-loaded, cached), with Expand All / Collapse All.

**Architecture:** A module-level `_CACHE` mirrors the loaded composite/sector/expansion state (single-user app), so `render()` repaints instantly from it. The sector table becomes a single rebuildable `_render_sector_table()` that interleaves industry rows under expanded sectors; industries are lazy-fetched off-thread by a new `_load_industries`. Two new pure transforms are TDD'd.

**Tech Stack:** NiceGUI (`ui.column` rebuild, `ui.icon`/`ui.button` toggles, `nicegui.run.io_bound`, `ui.timer`), pytest. Reuses `sectors_ref`, `week_month_from_closes`, `pct_color`.

**Design:** [`2026-06-14-sentiment-persistence-industries-design.md`](2026-06-14-sentiment-persistence-industries-design.md)

**Run tests:** from `D:\WebGUI Trading with Schwab\webgui` (PowerShell): `..\.venv\Scripts\python -m pytest -q`. venv: `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`.

**Current render() reference:** `webgui/pages/sentiment.py` — `render()` builds the sector section at ~466-480; `_apply_sectors` rebuilds `sector_box` inline at ~563-598; loaders + timers at ~600-637. `SEC_COLS` defines the 8 columns. `state = {"snaps","spy","sector"[, "loading","loading_sectors"]}`.

---

## Task 1: Pure transforms `sector_industry_etfs` + `industry_rows`

**Files:**
- Modify: `webgui/pages/sentiment.py` (append in the pure-transforms section, before `_load_snapshots`)
- Test: `webgui/tests/test_sentiment_sectors.py` (append)

**Step 1: Append failing tests** (reuse the existing `_sector_data()` fixture, which has an Information Technology → SMH industry row):

```python
def test_sector_industry_etfs():
    etfs = S.sector_industry_etfs(_sector_data(), "Information Technology")
    assert etfs == ["SMH"]
    assert S.sector_industry_etfs(_sector_data(), "Utilities") == []


def test_industry_rows_built():
    quotes = {"SMH": {"change_pct": 2.5}}
    trends = {"SMH": {"week_pct": 4.0, "month_pct": 9.0}}
    rows = S.industry_rows(_sector_data(), "Information Technology", quotes, trends)
    assert len(rows) == 1
    r = rows[0]
    assert r["etf"] == "SMH" and r["day"] == 2.5 and r["week"] == 4.0 and r["month"] == 9.0
    assert r["pcr"] is None and r["rrg"] is None
    assert r["label"] == "Semis"      # the industry label from the fixture
    assert r.get("is_industry") is True


def test_industry_rows_missing_data_blank():
    rows = S.industry_rows(_sector_data(), "Information Technology", {}, {})
    assert rows[0]["day"] is None and rows[0]["week"] is None and rows[0]["month"] is None
```

**Step 2: Run — expect FAIL.**

**Step 3: Append implementation:**

```python
def sector_industry_etfs(sector_data, sector_name):
    """Industry ETF symbols under a sector (kind=='industry', valid etf)."""
    out = []
    for r in sector_data:
        if r.get("kind") != "industry" or r.get("sector") != sector_name:
            continue
        etf = r.get("etf")
        if etf and etf != "n/a" and len(str(etf)) <= 6:
            out.append(etf)
    return out


def industry_rows(sector_data, sector_name, ind_quotes, ind_trends):
    """Indented rows for a sector's industries: day/week/month % only
    (pcr/rrg blank — industry option volume is too thin)."""
    rows = []
    for r in sector_data:
        if r.get("kind") != "industry" or r.get("sector") != sector_name:
            continue
        etf = r.get("etf")
        if not (etf and etf != "n/a" and len(str(etf)) <= 6):
            continue
        q = (ind_quotes or {}).get(etf) or {}
        t = (ind_trends or {}).get(etf) or {}
        rows.append({
            "label": r.get("label") or etf,
            "etf": etf,
            "desc": r.get("name") or "",
            "day": q.get("change_pct"),
            "week": t.get("week_pct"),
            "month": t.get("month_pct"),
            "pcr": None, "rrg": None,
            "is_industry": True,
        })
    return rows
```

**Step 4: Run — expect PASS (3 new). Then full suite.**

**Step 5: Commit**
```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment_sectors.py
git commit -m "feat(sentiment): sector_industry_etfs + industry_rows transforms"
```

---

## Task 2: Module-level cache + persistence + 300s timer

**Files:** Modify `webgui/pages/sentiment.py` (module level + `render()`).

**Step 1: Add the module-level cache** near the top (after the color constants, module scope — NOT inside render):

```python
# In-process cache so navigating away/back repaints instantly without re-fetch.
# Single-user app (see root CLAUDE.md); lost on server restart.
_CACHE = {"snaps": None, "spy": None, "sector": None,
          "expanded": set(), "industry": {}}
```

**Step 2: Seed `state` from `_CACHE` in `render()`** — change the `state` init to:

```python
    state = {
        "snaps": _CACHE["snaps"] or [],
        "spy": _CACHE["spy"] or [],
        "sector": _CACHE["sector"],
        "expanded": set(_CACHE["expanded"]),
        "industry": dict(_CACHE["industry"]),
    }
```

**Step 3: Mirror writes into `_CACHE`.** In `load()` after `state["snaps"], state["spy"] = snaps, spy`, add `_CACHE["snaps"], _CACHE["spy"] = snaps, spy`. In `load_sectors()` after `state["sector"] = …`, add `_CACHE["sector"] = state["sector"]`. (Task 3 adds the industry/expanded mirroring.)

**Step 4: Instant repaint + fetch-only-when-empty.** Replace the two timers at the end of `render()`:

```python
    # Instant repaint from cache on revisit; first-ever visit fetches.
    if state["snaps"]:
        _apply()
    if state["sector"]:
        _apply_sectors()
    if not state["snaps"]:
        ui.timer(0.1, lambda: load(with_sectors=True), once=True)
    ui.timer(300.0, load)   # composite-only auto-refresh (was 120s)
```

Note: `_apply()`/`_apply_sectors()` are defined above the timers in `render()`, so calling them at the end is fine. They must be safe to call synchronously (they are — pure widget updates).

**Step 5: Run full suite + import smoke** (`import main`). No new tests (wiring); confirm nothing breaks. Manually reason: on first visit cache empty → fetch; on return cache full → instant `_apply`/`_apply_sectors`, no fetch.

**Step 6: Commit**
```bash
git add webgui/pages/sentiment.py
git commit -m "feat(sentiment): persist page across navigation via module cache; 300s auto-refresh"
```

---

## Task 3: Industry loader + expandable sector table + Expand/Collapse All

**Files:** Modify `webgui/pages/sentiment.py`.

**Step 1: Add `_load_industries(etfs)`** near `_load_sector_perf`:

```python
def _load_industries(etfs):
    """Off-thread: quotes + week/month trends for a list of industry ETFs."""
    import proxy
    try:
        quotes = proxy.schwab_client.get_quotes(list(etfs)) or {}
    except Exception:
        quotes = {}
    trends = {}
    for etf in etfs:
        try:
            df = proxy.schwab_client.get_daily_history(etf, months=3)
        except Exception:
            df = None
        if df is None:
            continue
        cl = [float(c) for c in df["close"].tolist()]
        d3, wk, mo = week_month_from_closes(cl)
        trends[etf] = {"day3_pct": d3, "week_pct": wk, "month_pct": mo}
    return {"quotes": quotes, "trends": trends}
```

**Step 2: Replace the inline sector-table build in `_apply_sectors` with `_render_sector_table()`.**
Extract the `sector_box.clear()`…rows loop (currently in `_apply_sectors`) into a new inner function `_render_sector_table()` and have `_apply_sectors` call it. The new version:
- Prepends a **toggle column** (~24px) to the header and each sector row: a clickable `ui.icon` showing `keyboard_arrow_down` when expanded else `keyboard_arrow_right`, wired `on_click` to `_toggle_sector(sector_name)`.
- After emitting a sector row, if its `sector` is in `state["expanded"]`, emit its industry rows from `industry_rows(sd, sector, ind["quotes"], ind["trends"])` where `ind = state["industry"].get(sector)`; if `ind` is None show a single muted "loading…" row. Industry rows: indented (toggle column empty + left pad), label in the Sector column (e.g. `style("padding-left:18px")`), ETF, desc, Day/Week/Month colored via `pct_color`, blank P/C and RRG.

Keep `SEC_COLS` widths; add the toggle col. Example sector row inner structure (adapt to existing code):

```python
    def _render_sector_table():
        sec = state["sector"]
        if not sec:
            return
        sd = sec["sector_data"]
        rows = sector_table_rows(sd, sec["quotes"], sec["trends"], sec["pcr"], sec["quadrants"])
        sector_box.clear()
        with sector_box:
            with ui.row().classes("items-center w-full no-wrap gap-2 opacity-60 text-xs"):
                ui.label("").style("width:24px")
                for _f, hdr, w in SEC_COLS:
                    ui.label(hdr).style(f"width:{w}px")
            for r in rows:
                sector_name = r["sector"]
                expanded = sector_name in state["expanded"]
                with ui.row().classes("items-center w-full no-wrap gap-2 text-sm"):
                    ui.icon("keyboard_arrow_down" if expanded else "keyboard_arrow_right") \
                        .classes("cursor-pointer").style("width:24px") \
                        .on("click", lambda _e, s=sector_name: _toggle_sector(s))
                    ui.label(str(sector_name or "")).style("width:140px")
                    ui.label(str(r["etf"] or "")).style("width:50px")
                    ui.label(str(r["desc"] or "")).style(
                        "width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                    for fld in ("day", "week", "month"):
                        v = r[fld]
                        ui.label(f"{v:+.2f}%" if v is not None else "—") \
                            .style(f"width:70px;color:{pct_color(v)}")
                    pv = r["pcr"]
                    ui.label(f"{pv:.2f}" if pv is not None else "").style(
                        f"width:56px;color:{pcr_color(pv)}")
                    rv = r["rrg"]
                    ui.label(str(rv or "")).style(f"width:90px;color:{rrg_color(rv)}")
                if expanded:
                    ind = state["industry"].get(sector_name)
                    if ind is None:
                        with ui.row().classes("items-center w-full no-wrap gap-2 text-xs opacity-60"):
                            ui.label("").style("width:24px")
                            ui.label("loading…").style("width:140px")
                    else:
                        for ir in industry_rows(sd, sector_name, ind["quotes"], ind["trends"]):
                            with ui.row().classes("items-center w-full no-wrap gap-2 text-xs"):
                                ui.label("").style("width:24px")
                                ui.label(str(ir["label"] or "")).style("width:140px;padding-left:14px;opacity:0.85")
                                ui.label(str(ir["etf"] or "")).style("width:50px")
                                ui.label(str(ir["desc"] or "")).style(
                                    "width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;opacity:0.8")
                                for fld in ("day", "week", "month"):
                                    v = ir[fld]
                                    ui.label(f"{v:+.2f}%" if v is not None else "—") \
                                        .style(f"width:70px;color:{pct_color(v)}")
                                ui.label("").style("width:56px")
                                ui.label("").style("width:90px")
```

Then `_apply_sectors` keeps setting `summary_lbl`/`rotation_lbl` and refilling the component table, but delegates the table to `_render_sector_table()`.

**Step 3: Add the expand/collapse handlers + lazy fetch:**

```python
    async def _ensure_industry(sector_name):
        if sector_name in state["industry"]:
            return
        etfs = sector_industry_etfs(state["sector"]["sector_data"], sector_name)
        if not etfs:
            state["industry"][sector_name] = {"quotes": {}, "trends": {}}
        else:
            sec_spinner.visible = True
            try:
                state["industry"][sector_name] = await ng_run.io_bound(_load_industries, etfs)
            except Exception as e:  # noqa: BLE001
                ui.notify(f"Industry load failed: {e}", type="negative")
                state["industry"][sector_name] = {"quotes": {}, "trends": {}}
            finally:
                sec_spinner.visible = False
        _CACHE["industry"][sector_name] = state["industry"][sector_name]

    async def _toggle_sector(sector_name):
        if sector_name in state["expanded"]:
            state["expanded"].discard(sector_name)
        else:
            state["expanded"].add(sector_name)
            _render_sector_table()          # show "loading…" immediately
            await _ensure_industry(sector_name)
        _CACHE["expanded"] = set(state["expanded"])
        _render_sector_table()

    async def _expand_all():
        if not state["sector"]:
            return
        for r in sector_table_rows(state["sector"]["sector_data"], state["sector"]["quotes"],
                                   state["sector"]["trends"], state["sector"]["pcr"],
                                   state["sector"]["quadrants"]):
            state["expanded"].add(r["sector"])
        _render_sector_table()
        for s in list(state["expanded"]):
            await _ensure_industry(s)
        _CACHE["expanded"] = set(state["expanded"])
        _render_sector_table()

    def _collapse_all():
        state["expanded"].clear()
        _CACHE["expanded"] = set()
        _render_sector_table()
```

**Step 4: Add Expand All / Collapse All buttons** in the sector controls row (next to Refresh):

```python
        ui.button("Expand All", on_click=lambda: _expand_all()).props("flat dense")
        ui.button("Collapse All", on_click=lambda: _collapse_all()).props("flat dense")
```

**Step 5:** Ensure `_apply_sectors` calls `_render_sector_table()` (not the old inline build), and that on a fresh sector load stale expansion still renders (expanded sectors whose industry cache exists render; others lazily). When sectors are re-fetched via Refresh, keep `state["expanded"]` but note industry caches may be stale — acceptable; Refresh of sectors does not auto-refetch industries (they refetch on next expand after a Collapse, or leave as-is). Keep simple.

**Step 6: Run full suite + import smoke.** Confirm green + `import main` ok.

**Step 7: Commit**
```bash
git add webgui/pages/sentiment.py
git commit -m "feat(sentiment): expandable industry sub-rows (lazy + cached) + Expand/Collapse All"
```

---

## Task 4: Verify + docs

**Files:** Modify root `CLAUDE.md`.

**Step 1: Script-verify the industry loader against the live proxy** (the preview renderer is unreliable on the slow proxy). Write a temp `webgui/_verify_ind.py` that: sets sys.path (repo root + webgui), `from pages import sentiment as S`, loads `sectors_ref`, picks one sector (e.g. "Information Technology"), gets its `sector_industry_etfs`, calls `S._load_industries(etfs)`, prints `industry_rows(...)`. Run with `$env:PYTHONIOENCODING="utf-8"`. Confirm real Day/Week/Month % for industry ETFs. Delete the temp script after.

**Step 2: Best-effort browser check** — restart the `webgui` preview, navigate `/sentiment`; after it loads, use `preview_snapshot` (a11y) to confirm the toggle icons render and (if the proxy cooperates) expanding a sector shows industry rows. Then navigate to `/` and back to `/sentiment` and confirm via `preview_snapshot` that data is present immediately (persistence). If the proxy/renderer is too slow, rely on the script verification + tests and note it.

**Step 3: Update root `CLAUDE.md`** — extend the `/sentiment` row + dev-notes: persistence via module cache (300s composite auto-refresh), expandable industries (lazy + cached) with Expand/Collapse All. Remove the "expandable industry sub-rows" item from the follow-ups list. Bump test count.

**Step 4: Commit**
```bash
git add CLAUDE.md
git commit -m "docs: sentiment persistence + industry expansion built"
```

---

## Gotchas

- **No lazy `from scoring import`** anywhere (module-collision hazard) — none needed in this plan.
- `_CACHE` is module-level and shared across page instances — fine (single-user). Always assign a COPY into `state` (`set(...)`, `dict(...)`) so per-visit mutations don't alias the cache until explicitly mirrored back.
- `.on("click", handler)` for `ui.icon` — the handler receives an event arg; use `lambda _e, s=name: _toggle_sector(s)` to bind the sector name (avoid the late-binding closure bug).
- Expand All on the slow proxy fires many fetches — they run sequentially via `_ensure_industry`; the sector spinner shows. Acceptable (user-initiated).
- `_render_sector_table`/`_apply_sectors`/`_toggle_sector` are nested in `render()`; define `_render_sector_table` before `_apply_sectors` uses it (or assign via closure — Python resolves at call time, so order of definition within render() is fine as long as all defined before first call).
- `options-scanner` has ~2 known unrelated date-relative test failures — ignore.
