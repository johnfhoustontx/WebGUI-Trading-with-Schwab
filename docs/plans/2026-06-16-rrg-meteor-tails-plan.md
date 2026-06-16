# RRG Meteor Tails + Tighter FROM/INTO Spacing — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add fading 30-trading-day "meteor tails" to each sector on the Sector Rotation RRG scatter, and tighten the gap between the ROTATING FROM / ROTATING INTO columns by ~75–80%.

**Architecture:** The RRG engine already computes full RS-Ratio / RS-Momentum *series* per sector and keeps only the last point — we retain the last 30 points as a `tail` on each sector dict. That dict flows verbatim through the sentiment service's JSON cache (no typed contract strips it), so the Tier-3 page just reads the new field and draws a faded trailing trace behind each head dot. The spacing fix removes the `flex:1` that stretches the two columns apart.

**Tech Stack:** Python, pandas/numpy (engine), NiceGUI + Plotly (page), pytest.

**Design doc:** `docs/plans/2026-06-16-rrg-meteor-tails-design.md`

---

## Task 1: Engine emits a per-sector `tail`

**Files:**
- Modify: `sentiment-dashboard/sector_rotation_assessment.py` (add `TAIL_LENGTH`; extend `assess_sector`)
- Test: `sentiment-dashboard/tests/test_sector_rotation_tool.py`

**Step 1: Write the failing test**

Append to `sentiment-dashboard/tests/test_sector_rotation_tool.py`:

```python
def test_assess_from_close_series_includes_tail():
    sectors, bench = _synthetic_closes()
    a = rt.assess_from_close_series(sectors, bench, "2026-06-08")
    assert a is not None
    for s in a["sectors"]:
        tail = s.get("tail")
        assert isinstance(tail, list) and tail, f"{s['etf']} missing tail"
        # At most TAIL_LENGTH points, oldest -> newest.
        assert len(tail) <= rt.TAIL_LENGTH
        # Each point carries the same keys as the head reading.
        assert set(tail[0]) == {"rs_ratio", "rs_momentum"}
        # The newest tail point equals the current head reading.
        assert tail[-1]["rs_ratio"] == s["rs_ratio"]
        assert tail[-1]["rs_momentum"] == s["rs_momentum"]


def test_tail_length_constant_is_30():
    assert rt.TAIL_LENGTH == 30
```

**Step 2: Run test to verify it fails**

Run: `cd sentiment-dashboard ; python -m pytest tests/test_sector_rotation_tool.py::test_assess_from_close_series_includes_tail tests/test_sector_rotation_tool.py::test_tail_length_constant_is_30 -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'TAIL_LENGTH'` / `s.get("tail")` is None.

**Step 3: Write minimal implementation**

In `sentiment-dashboard/sector_rotation_assessment.py`, add the constant near the other RRG window constants (after `BENCHMARK = "SPY"`, around line 71):

```python
# Meteor-tail length: how many trailing daily RRG points to retain per sector
# for the RRG scatter trail (oldest -> newest, last == current reading).
TAIL_LENGTH = 30
```

In `assess_sector`, after the existing `paired` is built and the current values are read, build the tail from the SAME `paired` frame and add it to the returned dict. Replace the tail end of the function:

```python
    ratio_val = float(paired["ratio"].iloc[-1])
    mom_val = float(paired["mom"].iloc[-1])
    quadrant = classify_quadrant(ratio_val, mom_val)
    tail_df = paired.tail(TAIL_LENGTH)
    tail = [
        {"rs_ratio": round(float(r), 2), "rs_momentum": round(float(m), 2)}
        for r, m in zip(tail_df["ratio"], tail_df["mom"])
    ]
    return {
        "etf": etf,
        "name": SECTOR_ETFS.get(etf, etf),
        "rs_ratio": round(ratio_val, 2),
        "rs_momentum": round(mom_val, 2),
        "quadrant": quadrant,
        "direction": QUADRANT_DIRECTION[quadrant],
        "tail": tail,
    }
```

(Note: `round(ratio_val, 2)` already matches `tail[-1]["rs_ratio"]` because the
last tail row is the same `iloc[-1]` value, rounded the same way.)

**Step 4: Run tests to verify they pass**

Run: `cd sentiment-dashboard ; python -m pytest tests/test_sector_rotation_tool.py -v`
Expected: PASS (all existing rotation-tool tests + the 2 new ones).

**Step 5: Commit**

```bash
git add sentiment-dashboard/sector_rotation_assessment.py sentiment-dashboard/tests/test_sector_rotation_tool.py
git commit -m "feat(rotation): retain 30-pt RS-Ratio/RS-Mom tail per sector for RRG trail"
```

---

## Task 2: Page renders meteor-tail traces

**Files:**
- Modify: `webgui/pages/sentiment_rotation.py` (`rrg_scatter_figure` + a small helper)
- Test: `webgui/tests/test_sentiment_rotation.py`

**Step 1: Write the failing tests**

In `webgui/tests/test_sentiment_rotation.py`, first give the fixture tails. Replace the two sector dicts in `_assessment()` so each has a short `tail` (oldest→newest, last == head):

```python
        "sectors": [
            {"name": "Technology", "etf": "XLK", "rs_ratio": 101.5, "rs_momentum": 102.0,
             "quadrant": "Leading", "direction": "INTO",
             "tail": [{"rs_ratio": 100.2, "rs_momentum": 99.5},
                      {"rs_ratio": 100.9, "rs_momentum": 100.8},
                      {"rs_ratio": 101.5, "rs_momentum": 102.0}]},
            {"name": "Utilities", "etf": "XLU", "rs_ratio": 98.0, "rs_momentum": 97.0,
             "quadrant": "Lagging", "direction": "FROM",
             "tail": [{"rs_ratio": 99.1, "rs_momentum": 99.0},
                      {"rs_ratio": 98.5, "rs_momentum": 98.0},
                      {"rs_ratio": 98.0, "rs_momentum": 97.0}]},
        ],
```

Update the existing shape test (the head trace is no longer `data[0]` — tails precede it) and add new tail tests:

```python
def _head_trace(fig):
    return next(t for t in fig["data"] if t.get("mode") == "markers+text")


def test_rrg_scatter_figure_shape():
    fig = R.rrg_scatter_figure(_assessment())
    head = _head_trace(fig)
    assert head["type"] == "scatter"
    assert head["mode"].startswith("markers")
    assert set(head["x"]) == {101.5, 98.0}      # rs_ratio (heads)
    # crosshair reference lines at 100/100 present as shapes
    assert any(s.get("type") == "line" for s in fig["layout"].get("shapes", []))


def test_rrg_scatter_has_meteor_tail_per_sector():
    fig = R.rrg_scatter_figure(_assessment())
    tails = [t for t in fig["data"] if t is not _head_trace(fig)
             and t.get("mode") == "lines+markers"]
    assert len(tails) == 2                       # one per sector with a tail
    # Tails render BEHIND the head trace (head is last).
    assert fig["data"][-1] is _head_trace(fig)
    xlk_tail = next(t for t in tails if t["x"][-1] == 101.5)
    # Trail follows the sector's path, oldest -> newest, ending at the head.
    assert xlk_tail["x"] == [100.2, 100.9, 101.5]
    assert xlk_tail["y"] == [99.5, 100.8, 102.0]
    # Meteor fade: marker opacity ramps up toward the newest point.
    op = xlk_tail["marker"]["opacity"]
    assert op == sorted(op) and op[0] < op[-1]
    # Quadrant color (Leading -> green) on the markers; faint rgba line.
    assert xlk_tail["marker"]["color"] == R.CLR_GREEN
    assert xlk_tail["line"]["color"].startswith("rgba(")
    assert xlk_tail.get("showlegend") is False
    assert xlk_tail.get("hoverinfo") == "skip"


def test_rrg_scatter_handles_missing_tail():
    a = _assessment()
    for s in a["sectors"]:
        s.pop("tail", None)
    fig = R.rrg_scatter_figure(a)
    # No tail traces, but the head trace + crosshairs still render.
    assert all(t.get("mode") != "lines+markers" for t in fig["data"])
    assert _head_trace(fig)["x"]


def test_hex_to_rgba_helper():
    assert R._hex_to_rgba(R.CLR_GREEN, 0.28) == "rgba(102, 187, 106, 0.28)"
```

**Step 2: Run tests to verify they fail**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_sentiment_rotation.py -v`
Expected: FAIL — `_hex_to_rgba` missing; no `lines+markers` traces; (the rewritten `test_rrg_scatter_figure_shape` also fails until the helper-based lookup exists).

**Step 3: Write minimal implementation**

In `webgui/pages/sentiment_rotation.py`, add a helper above `rrg_scatter_figure`:

```python
def _hex_to_rgba(hex_color, alpha):
    """'#66bb6a' + 0.28 -> 'rgba(102, 187, 106, 0.28)'."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _tail_trace(sec):
    """Faded 'meteor' trail for one sector, or None if it has no tail.

    Oldest -> newest; marker opacity/size ramp up toward the current point so
    the head end is brightest. Colored by the sector's current quadrant."""
    tail = sec.get("tail") or []
    if len(tail) < 2:
        return None
    xs = [p["rs_ratio"] for p in tail]
    ys = [p["rs_momentum"] for p in tail]
    color = quadrant_color(sec.get("quadrant"))
    n = len(tail)
    # Ramp oldest(faint/small) -> newest(bright/larger).
    opacity = [round(0.12 + 0.73 * i / (n - 1), 3) for i in range(n)]
    size = [round(3.0 + 3.0 * i / (n - 1), 2) for i in range(n)]
    return {
        "type": "scatter", "mode": "lines+markers",
        "x": xs, "y": ys,
        "line": {"color": _hex_to_rgba(color, 0.28), "width": 1.5,
                 "shape": "spline"},
        "marker": {"color": color, "size": size, "opacity": opacity},
        "hoverinfo": "skip", "showlegend": False,
    }
```

Then in `rrg_scatter_figure`, build the head trace as today but assemble the
`data` list as `[...tails..., head]`. Replace the `return {...}` so the head dict
is named and prepended by the tails:

```python
def rrg_scatter_figure(a):
    """Plotly RRG scatter: faded 30-day meteor tail per sector + current dot,
    100/100 crosshair lines. Tails render behind the labeled head dots."""
    secs = a.get("sectors") or []
    xs = [s.get("rs_ratio") for s in secs]
    ys = [s.get("rs_momentum") for s in secs]
    colors = [quadrant_color(s.get("quadrant")) for s in secs]
    labels = [s.get("etf") for s in secs]
    line = {"color": "rgba(255,255,255,0.25)", "width": 1}
    head = {
        "type": "scatter", "mode": "markers+text",
        "x": xs, "y": ys, "text": labels, "textposition": "top center",
        "marker": {"size": 12, "color": colors},
        "hovertext": [f"{s.get('name')} — {s.get('quadrant')}" for s in secs],
        "hoverinfo": "text",
    }
    tails = [t for t in (_tail_trace(s) for s in secs) if t is not None]
    return {
        "data": [*tails, head],
        "layout": {
            "margin": {"l": 44, "r": 12, "t": 8, "b": 36}, "height": 360,
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
            "xaxis": {"title": "RS-Ratio", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "yaxis": {"title": "RS-Momentum", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "shapes": [
                {"type": "line", "xref": "x", "yref": "paper", "x0": 100, "x1": 100,
                 "y0": 0, "y1": 1, "line": line},
                {"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1,
                 "y0": 100, "y1": 100, "line": line},
            ],
        },
    }
```

**Step 4: Run tests to verify they pass**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_sentiment_rotation.py -v`
Expected: PASS (all rotation-page tests incl. the 4 new/updated ones).

**Step 5: Commit**

```bash
git add webgui/pages/sentiment_rotation.py webgui/tests/test_sentiment_rotation.py
git commit -m "feat(rotation): RRG meteor tails — faded 30-day quadrant-colored trail per sector"
```

---

## Task 3: Tighten ROTATING FROM / INTO spacing

**Files:**
- Modify: `webgui/pages/sentiment_rotation.py` (`_render`, the `cols_box` loop)

> Pure-layout change (NiceGUI widget wiring) — not unit-tested; verified in the
> browser in Task 4.

**Step 1: Make the change**

In `_render`, the columns currently stretch via `flex:1`. Change the side-column
build so columns hug content, and tighten the row gap.

Change the `cols_box` definition (near the top of `render()`):

```python
    cols_box = ui.row().classes("no-wrap items-start gap-8 q-mt-sm")
```

(from `("w-full no-wrap gap-6 q-mt-sm")` — drop `w-full` so the row hugs its two
content-width columns; `gap-8` = 32px sits them close together.)

And in the `_render` loop, drop the `flex:1` stretch on each side column:

```python
                with ui.column().classes("items-start"):
```

(from `ui.column().classes("items-start").style("flex:1")`.)

**Step 2: Commit (after Task 4 verification confirms the gap looks right)**

```bash
git add webgui/pages/sentiment_rotation.py
git commit -m "fix(rotation): tighten ROTATING FROM/INTO column gap (~75-80% less)"
```

---

## Task 4: Browser verification

**Goal:** Confirm both changes render correctly against the running stack.

**Preconditions:** Memurai (:6379), schwab-proxy (:8100), and `sentiment_svc`
(:8210) must be running, and the rotation cache populated (hit **Refresh** on the
page if it shows "Waiting for sentiment service…"). The page reads
`cache:sentiment:rotation`; the new `tail` field only appears after
`sentiment_svc` re-runs the assessment with the Task-1 engine change — so
**restart `sentiment_svc`** after Task 1, then Refresh.

**Steps:**
1. `preview_start` the `webgui` dev server (:8500), navigate to `/sentiment/rotation`.
2. If headline shows "Waiting…", click **Refresh** and wait for the version-poll repaint.
3. `preview_screenshot` — verify:
   - ROTATING FROM and ROTATING INTO sit close together (tight gap, not half-width apart).
   - Each RRG dot has a faded trailing tail in its quadrant color, brightest at the dot.
4. `preview_console_logs` — no Plotly/JS errors.
5. If the gap still looks too wide or too tight, adjust `gap-8` (try `gap-6` / `gap-10`) and re-screenshot before committing Task 3.

> If the services are not running in this environment, note that verification was
> deferred and the unit tests (Tasks 1–2) are the proof of correctness for the
> engine + figure-builder; the spacing change is low-risk CSS.

---

## Final: full suites green

```bash
cd sentiment-dashboard ; python -m pytest tests -q
cd webgui ; ..\.venv\Scripts\python -m pytest -q
```

Expected: both green (sentiment-dashboard has ~2 known date-relative failures
carried from the source repo — unrelated; do not "fix"). Then update the root
`CLAUDE.md` Sector Rotation row to mention the RRG meteor tails, and commit.
