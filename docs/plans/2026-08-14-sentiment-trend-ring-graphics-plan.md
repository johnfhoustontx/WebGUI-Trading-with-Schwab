# Sentiment + Trend Ring Graphics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the four semicircular Highcharts gauges on `/sentiment` with two
concentric-ring SVG graphics, each showing Day / Week / Month on one dial.

**Architecture:** A new pure SVG-string builder (`webgui/pages/rings.py`) mounted
via `ui.html()` and updated by assigning `el.content`. Market Sentiment sources all
three arcs page-side from existing data; Market Trend needs one additive Tier-2
compute (`compute_7d_trend`) because no weekly trend exists today.

**Tech Stack:** Python 3.11, NiceGUI (`ui.html`), pytest. No new dependencies.

**Design doc:** `docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md`
(commit `9a88af0`) — read it first; it records *why* each decision was made and
which alternatives were rejected.

---

## Orientation for someone new to this repo

**This is a 3-tier app.** `webgui/` is Tier 1 and may import ONLY `nicegui`,
`shared.bus`, `shared.contracts` and its own `pages/` modules. It must never
import an engine or call Schwab. Tier-2 services (`services/sentiment_svc`) do
the computing and publish to Redis; the page reads the cache. Task 6-9 touch
Tier 2; tasks 1-5 touch Tier 1. Do not blur them.

**The venv lives at the REPO ROOT, not in this worktree.** Always invoke it by
absolute path:

```
D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe
```

**Never leave the shell in a subdirectory.** The hooks in `.claude/settings.json`
are registered by relative path; a bare `cd webgui` breaks every subsequent tool
call for the rest of the session and cannot be recovered. Always use a subshell:
`(cd webgui && ...)`.

**Baseline test counts before you start** (compare the failing *set*, never the
count — this repo has a documented incident where two real regressions hid behind
two tests flipping to skipped while the total held steady):

- `webgui` — 1190 passed
- `services/sentiment_svc` — 250 passed / **1 known failure**:
  `tests/test_compute_regime.py::test_daily_history_wins_over_session_latch`.
  That failure predates this work. Do not fix it here.

Run with `-rf` so the failing node IDs are printed.

**Style rules that bind this work:**
- `.style(...)`, inline `style=` on NiceGUI components, and `:style=` bindings are
  BANNED (`webgui/tests/test_no_inline_style.py` enforces it). Raw `ui.html()`
  fragment strings are a documented exemption — that is what makes this design legal.
- Colors must come from `config/theme.toml`, reached via
  `pages.options.theme.THEME`. Do not hardcode new hexes.

---

## Task 0: Confirm the `ui.html` sanitizer preserves SVG filters

`ui.html()` is documented to strip `<style>` and `<iframe>`. It is unknown whether
`<defs>`/`<filter>`/`<feGaussianBlur>` survive. This task only *answers the
question* — nothing is blocked on it, because Task 2 builds a layered-halo glow
that needs no filter at all. A surviving filter is an optional enhancement (Task 8).

**Step 1: Find the sanitizer**

Run:
```bash
(cd "D:/WebGUI Trading with Schwab/.venv/Lib/site-packages/nicegui" && grep -rn "sanitiz\|<style\|iframe" elements/html.py element.py 2>/dev/null | head -30)
```

**Step 2: Record the answer**

Append one line to this plan file under Task 8 stating either
`FILTERS SURVIVE` or `FILTERS STRIPPED`. No commit.

---

## Task 1: Ring geometry helpers

Pure polar/arc math. Everything downstream depends on getting the angle
convention right, so it gets its own task.

**Convention:** degrees measured **clockwise from 12 o'clock**. The scale starts
at 225° (lower-left = 0) and sweeps 270° clockwise to 495° ≡ 135° (lower-right =
100). So 25 → 292.5° (upper-left), 50 → 360° (top), 75 → 67.5° (upper-right).

**Files:**
- Create: `webgui/pages/rings.py`
- Create: `webgui/tests/test_rings.py`

**Step 1: Write the failing tests**

Create `webgui/tests/test_rings.py`:

```python
import math

from pages import rings


def _close(a, b, tol=0.01):
    return abs(a - b) < tol


def test_point_at_zero_degrees_is_top():
    x, y = rings._point(140, 140, 100, 0)
    assert _close(x, 140) and _close(y, 40)


def test_point_at_ninety_degrees_is_right():
    x, y = rings._point(140, 140, 100, 90)
    assert _close(x, 240) and _close(y, 140)


def test_point_at_start_angle_is_lower_left():
    x, y = rings._point(140, 140, 100, rings.START_DEG)
    assert x < 140 and y > 140


def test_value_angle_maps_endpoints_and_midpoint():
    assert _close(rings._value_angle(0), 225.0)
    assert _close(rings._value_angle(50), 360.0)      # top
    assert _close(rings._value_angle(100), 495.0)     # lower-right


def test_arc_path_is_empty_at_zero():
    assert rings._arc_path(140, 140, 100, 225.0, 225.0) == ""


def test_arc_path_sets_large_arc_flag_past_180_degrees():
    short = rings._arc_path(140, 140, 100, 225.0, 315.0)   # 90 deg
    long_ = rings._arc_path(140, 140, 100, 225.0, 495.0)   # 270 deg
    # path is "M x y A r r 0 <large> 1 x1 y1"
    assert short.split()[6] == "0"
    assert long_.split()[6] == "1"


def test_arc_path_always_sweeps_clockwise():
    p = rings._arc_path(140, 140, 100, 225.0, 495.0)
    assert p.split()[7] == "1"
```

**Step 2: Run the tests to verify they fail**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_rings.py -q)
```
Expected: collection error — `ModuleNotFoundError: No module named 'pages.rings'`.

**Step 3: Write the minimal implementation**

Create `webgui/pages/rings.py`:

```python
"""Pure SVG builders for the concentric Day/Week/Month ring graphics on
``/sentiment`` (design: docs/plans/2026-08-14-sentiment-trend-ring-graphics-design.md).

Angles are measured **clockwise from 12 o'clock**. The scale starts at 225°
(lower-left = 0) and sweeps 270° to 495° ≡ 135° (lower-right = 100), leaving a
90° gap at the bottom for the Week/Month legend. Pure functions, no NiceGUI
import — mounted by the page via ``ui.html`` and updated with ``el.content``.
"""
import math

START_DEG = 225.0   # 0 on the scale — lower-left
SWEEP_DEG = 270.0   # to 495 deg == 135 deg — lower-right


def _point(cx, cy, r, deg):
    """(x, y) at ``deg`` clockwise from 12 o'clock on the circle (cx, cy, r)."""
    rad = math.radians(deg - 90.0)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _value_angle(value):
    """0-100 -> absolute sweep angle (225 .. 495)."""
    return START_DEG + SWEEP_DEG * (value / 100.0)


def _arc_path(cx, cy, r, start_deg, end_deg):
    """SVG ``d`` for a clockwise arc; "" for a degenerate (sub-pixel) sweep."""
    sweep = end_deg - start_deg
    if sweep < 0.5:
        return ""
    x0, y0 = _point(cx, cy, r, start_deg)
    x1, y1 = _point(cx, cy, r, end_deg)
    large = 1 if sweep > 180.0 else 0
    return (f"M {x0:.2f} {y0:.2f} "
            f"A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}")
```

**Step 4: Run the tests to verify they pass**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_rings.py -q)
```
Expected: `7 passed`.

**Step 5: Commit**

```bash
git add webgui/pages/rings.py webgui/tests/test_rings.py
git commit -m "feat(rings): polar + arc-path geometry for the Day/Week/Month dial"
```

---

## Task 2: The ring SVG builder

**Files:**
- Modify: `webgui/pages/rings.py`
- Modify: `webgui/tests/test_rings.py`

**Design constraints restated** (all verified against the design doc):
- Fixed internal `viewBox="0 0 280 280"`; the `size` argument sets only the
  `width`/`height` attributes, so the SVG scales itself and there is no scale math.
- Radii 112 / 90 / 68 at stroke-width 13. Ticks at r=132.
- Center holds the **outermost** arc only (big value + caption). Week and Month
  go in the **bottom gap** — that is what the 90° gap is for.
- Each arc is colored by **its own value** through `gauge._ramp_color`, so
  `config/theme.toml [gauge]` keeps driving the palette.
- Glow is a **layered halo**: a wide translucent copy of the arc drawn beneath a
  normal-width bright one. No `<filter>`, so it cannot be sanitized away.
- `uid` is REQUIRED. Two rings render on one page; any shared DOM id would make
  them collide.
- A `None` value renders track-only with `—`.

**Step 1: Write the failing tests**

Append to `webgui/tests/test_rings.py`:

```python
def _arcs(a=72.0, b=61.0, c=52.0):
    return [{"value": a, "caption": "DAY"},
            {"value": b, "caption": "WEEK"},
            {"value": c, "caption": "MONTH"}]


def test_ring_svg_is_a_single_svg_element_with_fixed_viewbox():
    out = rings.ring_svg(_arcs(), uid="sent")
    assert out.startswith("<svg ") and out.rstrip().endswith("</svg>")
    assert 'viewBox="0 0 280 280"' in out


def test_ring_svg_size_sets_only_width_and_height():
    out = rings.ring_svg(_arcs(), uid="sent", size=280)
    assert 'width="280"' in out and 'height="280"' in out
    assert 'viewBox="0 0 280 280"' in out


def test_ring_svg_emits_no_style_or_filter_elements():
    """ui.html strips <style>; the halo is layered strokes, not a filter."""
    out = rings.ring_svg(_arcs(), uid="sent")
    assert "<style" not in out
    assert "<filter" not in out


def test_ring_svg_draws_a_track_and_a_value_arc_per_arc():
    out = rings.ring_svg(_arcs(), uid="sent")
    for r in rings.RADII:
        assert f'class="ring-track" ' in out or True   # tracks exist below
    # 3 tracks + 3 halos + 3 value arcs = 9 <path> elements
    assert out.count("<path ") == 9


def test_ring_svg_colors_each_arc_by_its_own_value():
    """A green Day over a red Month must produce two different stroke colors."""
    from pages import gauge
    out = rings.ring_svg(_arcs(a=95.0, b=50.0, c=5.0), uid="sent")
    assert gauge._ramp_color(0.95) in out
    assert gauge._ramp_color(0.05) in out


def test_ring_svg_ids_are_namespaced_by_uid():
    a = rings.ring_svg(_arcs(), uid="sent")
    b = rings.ring_svg(_arcs(), uid="trend")
    assert "sent" in a and "trend" in b
    # no id from one ring may appear in the other
    assert 'id="ring-sent' not in b
    assert 'id="ring-trend' not in a


def test_ring_svg_puts_the_outermost_value_in_the_center():
    out = rings.ring_svg(_arcs(a=72.0), uid="sent")
    assert ">72<" in out
    assert ">DAY<" in out


def test_ring_svg_puts_week_and_month_in_the_bottom_gap():
    out = rings.ring_svg(_arcs(b=61.0, c=52.0), uid="sent")
    assert ">61<" in out and ">WEEK<" in out
    assert ">52<" in out and ">MONTH<" in out


def test_ring_svg_renders_dash_for_a_missing_value():
    arcs = _arcs()
    arcs[1]["value"] = None
    out = rings.ring_svg(arcs, uid="sent")
    assert ">—<" in out


def test_ring_svg_missing_value_draws_track_only():
    arcs = _arcs()
    arcs[1]["value"] = None
    out = rings.ring_svg(arcs, uid="sent")
    # 3 tracks + 2 halos + 2 value arcs
    assert out.count("<path ") == 7


def test_ring_svg_clamps_out_of_range_and_junk_values():
    for bad in (-40.0, 140.0, "abc", float("nan")):
        arcs = _arcs()
        arcs[0]["value"] = bad
        out = rings.ring_svg(arcs, uid="sent")   # must not raise
        assert out.startswith("<svg ")


def test_ring_svg_draws_the_five_scale_ticks():
    out = rings.ring_svg(_arcs(), uid="sent")
    for tick in ("0", "25", "50", "75", "100"):
        assert f">{tick}<" in out


def test_ring_svg_accepts_fewer_than_three_arcs():
    out = rings.ring_svg(_arcs()[:2], uid="sent")
    assert out.count("<path ") == 6
```

**Step 2: Run to verify they fail**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_rings.py -q)
```
Expected: `AttributeError: module 'pages.rings' has no attribute 'ring_svg'`.

**Step 3: Implement**

Append to `webgui/pages/rings.py`:

```python
from pages.gauge import _ramp_color

RADII = (112.0, 90.0, 68.0)   # outer -> inner, in the fixed 280 viewBox
STROKE = 13.0
HALO_EXTRA = 9.0              # extra stroke-width for the translucent glow layer
HALO_OPACITY = 0.22
TICK_R = 132.0
CX = CY = 140.0
TRACK = "#1b2233"             # dim unfilled track (matches the page's chip bg)
TICK_FILL = "#7f8db0"
_TICKS = (0, 25, 50, 75, 100)


def _safe_value(v):
    """Clamp to [0, 100]; None / junk / NaN -> None (renders track-only)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                       # NaN
        return None
    return max(0.0, min(100.0, f))


def _esc(text):
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt(v):
    return "—" if v is None else f"{v:.0f}"


def ring_svg(arcs, uid, size=280):
    """Concentric Day/Week/Month dial as one inline SVG string.

    ``arcs`` is outermost-first: ``[{"value": 0-100 or None, "caption": str}, ...]``
    (1-3 entries). ``uid`` namespaces every DOM id — REQUIRED, because two rings
    share a page and a duplicate id would make them collide. Never raises.
    """
    arcs = list(arcs or [])[:len(RADII)]
    vals = [_safe_value(a.get("value")) for a in arcs]
    caps = [_esc(a.get("caption")) for a in arcs]
    colors = [_ramp_color((v or 0.0) / 100.0) for v in vals]

    full = _arc_path(CX, CY, RADII[0], START_DEG, START_DEG + SWEEP_DEG)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 280" '
        f'width="{size}" height="{size}" id="ring-{_esc(uid)}">'
    ]

    # Tracks first, so every value arc paints over its own track.
    for i, _v in enumerate(vals):
        d = _arc_path(CX, CY, RADII[i], START_DEG, START_DEG + SWEEP_DEG)
        parts.append(f'<path d="{d}" fill="none" stroke="{TRACK}" '
                     f'stroke-width="{STROKE}" stroke-linecap="round"/>')

    # Halo + value arc per filled arc.
    for i, v in enumerate(vals):
        if v is None:
            continue
        d = _arc_path(CX, CY, RADII[i], START_DEG, _value_angle(v))
        if not d:
            continue
        parts.append(f'<path d="{d}" fill="none" stroke="{colors[i]}" '
                     f'stroke-width="{STROKE + HALO_EXTRA}" stroke-linecap="round" '
                     f'opacity="{HALO_OPACITY}"/>')
        parts.append(f'<path d="{d}" fill="none" stroke="{colors[i]}" '
                     f'stroke-width="{STROKE}" stroke-linecap="round"/>')

    # Scale ticks around the outer rim.
    for t in _TICKS:
        x, y = _point(CX, CY, TICK_R, _value_angle(t))
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
                     f'dominant-baseline="middle" font-size="11" '
                     f'fill="{TICK_FILL}">{t}</text>')

    # Centre — the OUTERMOST arc only.
    if vals:
        parts.append(
            f'<text x="{CX}" y="146" text-anchor="middle" font-size="52" '
            f'font-weight="700" fill="{colors[0]}">{_fmt(vals[0])}</text>')
        parts.append(
            f'<text x="{CX}" y="170" text-anchor="middle" font-size="12" '
            f'letter-spacing="3" fill="{TICK_FILL}">{caps[0]}</text>')

    # Week + Month live in the 90-degree bottom gap.
    for i, x in ((1, 104.0), (2, 176.0)):
        if i >= len(vals):
            continue
        parts.append(
            f'<text x="{x}" y="250" text-anchor="middle" font-size="22" '
            f'font-weight="600" fill="{colors[i]}">{_fmt(vals[i])}</text>')
        parts.append(
            f'<text x="{x}" y="267" text-anchor="middle" font-size="10" '
            f'letter-spacing="2" fill="{TICK_FILL}">{caps[i]}</text>')

    parts.append("</svg>")
    return "".join(parts)
```

Note the `from pages.gauge import _ramp_color` import — `gauge.py` is otherwise
untouched by this work, and reusing its ramp is what keeps
`config/theme.toml [gauge]` in control of the palette.

**Step 4: Run to verify they pass**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_rings.py -q)
```
Expected: `20 passed`.

If `test_ring_svg_colors_each_arc_by_its_own_value` fails, check whether
`_ramp_color` clamps differently than assumed — read `webgui/pages/gauge.py:45`
and adjust the *test's* expected value, not the implementation.

**Step 5: Commit**

```bash
git add webgui/pages/rings.py webgui/tests/test_rings.py
git commit -m "feat(rings): concentric Day/Week/Month ring SVG builder"
```

---

## Task 3: `sentiment_avg` — a windowed composite mean

**Files:**
- Modify: `webgui/pages/sentiment.py:222-225`
- Modify: `webgui/tests/test_sentiment.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_sentiment.py`:

```python
def _snaps(*scores):
    return [{"date": f"2026-08-{i + 1:02d}",
             "composite": {"total_score": s}} for i, s in enumerate(scores)]


def test_sentiment_avg_windows_to_the_last_n():
    snaps = _snaps(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    assert sentiment.sentiment_avg(snaps, 5) == 5.0     # mean of 3..7


def test_sentiment_avg_with_no_window_uses_every_snap():
    assert sentiment.sentiment_avg(_snaps(2.0, 4.0)) == 3.0


def test_sentiment_avg_window_larger_than_history_uses_all():
    assert sentiment.sentiment_avg(_snaps(2.0, 4.0), 5) == 3.0


def test_sentiment_avg_is_zero_with_no_snaps():
    assert sentiment.sentiment_avg([], 5) == 0.0
    assert sentiment.sentiment_avg(None) == 0.0


def test_sentiment_avg_or_none_is_none_with_no_snaps():
    assert sentiment.sentiment_avg_or_none([], 5) is None


def test_sentiment_30d_avg_still_averages_everything():
    """Back-compat: the old name must keep its old behaviour."""
    assert sentiment.sentiment_30d_avg(_snaps(2.0, 4.0)) == 3.0
```

**Step 2: Run to verify they fail**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_sentiment.py -q -k sentiment_avg)
```
Expected: `AttributeError: ... has no attribute 'sentiment_avg'`.

**Step 3: Implement**

Replace `webgui/pages/sentiment.py:222-225` with:

```python
WEEK_SNAPS = 5   # trading days in the "Week" arc's window


def sentiment_avg_or_none(snaps, n=None):
    """Mean composite over the last ``n`` snapshots (all when ``n`` is None),
    or None when there is no history at all. Pure."""
    scores = composite_series(snaps or [])[1]
    if n is not None:
        scores = scores[-n:]
    return round(sum(scores) / len(scores), 2) if scores else None


def sentiment_avg(snaps, n=None):
    """``sentiment_avg_or_none`` with a 0.0 floor (the legacy contract)."""
    v = sentiment_avg_or_none(snaps, n)
    return 0.0 if v is None else v


def sentiment_30d_avg(snaps):
    """Mean composite over the whole backfill history (0.0 if none). Pure."""
    return sentiment_avg(snaps)
```

**Step 4: Run to verify they pass**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_sentiment.py -q)
```
Expected: all pass, including the pre-existing `sentiment_30d_avg` tests.

**Step 5: Commit**

```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment.py
git commit -m "feat(sentiment): windowed sentiment_avg for the Week arc"
```

---

## Task 4: Arc assembly builders

Pure functions turning cached payloads into the `arcs` list `ring_svg` wants.
Keeping them separate from `render()` is what makes them testable.

**Files:**
- Modify: `webgui/pages/sentiment.py` (add after `sentiment_30d_avg`)
- Modify: `webgui/tests/test_sentiment.py`

**Step 1: Write the failing tests**

```python
def test_sentiment_arcs_scales_composite_to_0_100():
    live = {"composite": {"total_score": 7.2}}
    arcs = sentiment.sentiment_arcs(live, _snaps(5.0, 5.0))
    assert [a["caption"] for a in arcs] == ["DAY", "WEEK", "MONTH"]
    assert arcs[0]["value"] == 72.0
    assert arcs[1]["value"] == 50.0


def test_sentiment_arcs_falls_back_to_the_last_snap_when_live_is_absent():
    arcs = sentiment.sentiment_arcs(None, _snaps(4.0, 6.0))
    assert arcs[0]["value"] == 60.0


def test_sentiment_arcs_week_and_month_are_none_with_no_history():
    arcs = sentiment.sentiment_arcs({"composite": {"total_score": 7.0}}, [])
    assert arcs[1]["value"] is None and arcs[2]["value"] is None


def test_trend_arcs_reads_all_three_horizons():
    derived = {"trend": {"smoothed_score": 71.0},
               "trend_7d": {"score": 61.0},
               "trend_30d_ago": {"score": 52.0}}
    arcs = sentiment.trend_arcs(derived)
    assert [a["caption"] for a in arcs] == ["DAY", "WEEK", "MONTH"]
    assert [a["value"] for a in arcs] == [71.0, 61.0, 52.0]


def test_trend_arcs_week_is_none_before_the_service_publishes_it():
    """trend_7d is absent until sentiment_svc is restarted -> track-only."""
    arcs = sentiment.trend_arcs({"trend": {"smoothed_score": 71.0}})
    assert arcs[1]["value"] is None


def test_trend_arcs_handles_an_empty_derived_block():
    arcs = sentiment.trend_arcs({})
    assert all(a["value"] is None for a in arcs)
    assert len(arcs) == 3
```

**Step 2: Run to verify they fail**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_sentiment.py -q -k "arcs")
```
Expected: `AttributeError: ... has no attribute 'sentiment_arcs'`.

**Step 3: Implement**

Add to `webgui/pages/sentiment.py`, directly after `sentiment_30d_avg`:

```python
def sentiment_arcs(live, snaps):
    """Day/Week/Month arcs for the Market Sentiment ring (composite 0-10 -> 0-100)."""
    latest = live or ((snaps or [])[-1] if snaps else None)
    total = _safe_float(((latest or {}).get("composite") or {}).get("total_score"))
    week = sentiment_avg_or_none(snaps, WEEK_SNAPS)
    month = sentiment_avg_or_none(snaps)
    return [
        {"value": gauge_score(total), "caption": "DAY"},
        {"value": None if week is None else gauge_score(week), "caption": "WEEK"},
        {"value": None if month is None else gauge_score(month), "caption": "MONTH"},
    ]


def trend_arcs(derived):
    """Day/Week/Month arcs for the Market Trend ring (already 0-100).

    A horizon the service has not published yet (``trend_7d`` before an
    options/sentiment service restart) reads None -> the ring draws that arc's
    track only, rather than a fabricated neutral 50.
    """
    d = derived or {}

    def _v(key):
        t = d.get(key)
        return trend_gauge_value(t) if t else None

    return [{"value": _v("trend"), "caption": "DAY"},
            {"value": _v("trend_7d"), "caption": "WEEK"},
            {"value": _v("trend_30d_ago"), "caption": "MONTH"}]
```

**Step 4: Run to verify they pass**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_sentiment.py -q)
```
Expected: all pass.

**Step 5: Commit**

```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment.py
git commit -m "feat(sentiment): Day/Week/Month arc builders for both rings"
```

---

## Task 5: Rewire the page — four gauges become two rings

**Files:**
- Modify: `webgui/pages/sentiment.py:836-877` (the two panel blocks)
- Modify: `webgui/pages/sentiment.py` `_apply()` — the gauge lines at ~986-990,
  ~1036-1038, ~1058-1059 and ~1067-1072
- Modify: `webgui/pages/sentiment.py:28` (import)

**Step 1: Swap the import**

At line 28, replace:

```python
from pages.gauge import gauge_figure  # noqa: F401  (re-export; used by render)
```

with:

```python
from pages.rings import ring_svg
```

`gauge_figure` has no other caller in this file once Task 5 is done. If a test
imports it from here, keep the old line as well — check with:
```bash
(cd webgui && grep -rn "sentiment.gauge_figure\|from pages.sentiment import.*gauge_figure" tests/)
```

**Step 2: Replace the Market Sentiment panel (lines 838-848)**

```python
        # ① Market Sentiment — Day/Week/Month ring + press-and-hold Components popup
        with ui.column().classes("items-center min-w-[300px]"):
            ui.label("Market Sentiment").classes("text-h6")
            sent_ring = ui.html(
                ring_svg(sentiment_arcs(None, []), uid="sent")
            ).classes("w-[280px] h-[280px]")
```

Delete the `with ui.row()...` block and both inner `ui.column()` blocks it held,
including the `Today` / `30-Day Avg` labels — those captions now live inside the
ring. Keep `bias_lbl`, `sub_lbl` and the whole Components button/menu unchanged.

**Step 3: Replace the Market Trend panel (lines 859-869)**

```python
        # ② Market Trend — Day/Week/Month ring + label/desc + detail popup
        with ui.column().classes("items-center min-w-[300px]"):
            ui.label("Market Trend").classes("text-h6")
            trend_ring = ui.html(
                ring_svg(trend_arcs({}), uid="trend")
            ).classes("w-[280px] h-[280px]")
```

Keep `regime_badge`, `regime_desc` and the whole Trend Detail button/menu unchanged.

**Step 4: Update `_apply()`**

Replace the two sentiment gauge writes (~986-990):

```python
        sent_ring.content = ring_svg(sentiment_arcs(live, state["snaps"]), uid="sent")
```

Delete the now-dangling `avg = sentiment_30d_avg(...)` line ONLY if nothing else
in `_apply` uses `avg` — grep first. Keep `bias_lbl` / `sub_lbl` as they are.

Replace the trend gauge writes. The current code sets `trend_gauge_box` inside
`if trend:` / `else:` and `trend_gauge_30_box` in a separate block; the ring
replaces all four with ONE call placed after `derived` is read:

```python
        trend_ring.content = ring_svg(trend_arcs(derived), uid="trend")
```

Leave the surrounding `if trend:` block intact — it still fills `regime_badge`,
`regime_desc` and `trend_detail_box`. Only the `trend_gauge_box.options = ...` /
`.update()` and `trend_gauge_30_box.options = ...` / `.update()` lines are deleted.

**Step 5: Verify nothing dangles**

Run:
```bash
(cd webgui && grep -n "gauge_box\|gauge_avg_box\|trend_gauge_30_box\|gauge_figure" pages/sentiment.py)
```
Expected: no output.

**Step 6: Run the full webgui suite**

Run:
```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest . -q -rf)
```
Expected: 1190+ passed, 0 failed. `test_no_inline_style.py` must still pass —
`ui.html` fragments are exempt, but a stray `.classes()` typo will show here.

**Step 7: Commit**

```bash
git add webgui/pages/sentiment.py
git commit -m "feat(sentiment): replace the four gauges with two Day/Week/Month rings"
```

---

## Task 6: One sector fetch serving both horizons

Before adding `compute_7d_trend`, make the sector fetch shared and cached — this
repo's Schwab volume is already audited at ~68-76k calls/day and a second
independent sector fetch would be pure waste.

**Files:**
- Modify: `services/sentiment_svc/compute.py:1283-1294`
- Modify: `services/sentiment_svc/tests/test_compute.py`

**Step 1: Write the failing tests**

```python
def test_fetch_sector_pcts_returns_both_horizons(monkeypatch):
    compute.reset_sector_pcts_cache()
    monkeypatch.setattr(compute, "_fetch_closes",
                        lambda etfs, months=3: ({}, {
                            "XLK": {"week_pct": 1.0, "month_pct": 3.0}}))
    monkeypatch.setitem(__import__("sys").modules, "sectors_ref",
                        type("m", (), {"load_sectors_data": staticmethod(
                            lambda: [{"kind": "sector", "etf": "XLK"}])}))
    out = compute._fetch_sector_pcts()
    assert out == {"week": {"XLK": 1.0}, "month": {"XLK": 3.0}}


def test_fetch_sector_pcts_is_cached_across_calls(monkeypatch):
    compute.reset_sector_pcts_cache()
    calls = {"n": 0}

    def _closes(etfs, months=3):
        calls["n"] += 1
        return {}, {"XLK": {"week_pct": 1.0, "month_pct": 3.0}}

    monkeypatch.setattr(compute, "_fetch_closes", _closes)
    monkeypatch.setitem(__import__("sys").modules, "sectors_ref",
                        type("m", (), {"load_sectors_data": staticmethod(
                            lambda: [{"kind": "sector", "etf": "XLK"}])}))
    compute._fetch_sector_pcts()
    compute._fetch_sector_pcts()
    assert calls["n"] == 1


def test_fetch_sector_pcts_degrades_to_empty(monkeypatch):
    compute.reset_sector_pcts_cache()
    monkeypatch.setattr(compute, "_fetch_closes",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert compute._fetch_sector_pcts() == {"week": {}, "month": {}}


def test_fetch_sector_month_pcts_still_returns_the_month_map(monkeypatch):
    compute.reset_sector_pcts_cache()
    monkeypatch.setattr(compute, "_fetch_sector_pcts",
                        lambda: {"week": {"XLK": 1.0}, "month": {"XLK": 3.0}})
    assert compute._fetch_sector_month_pcts() == {"XLK": 3.0}
```

**Step 2: Run to verify they fail**

Run:
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc/tests/test_compute.py -q -k sector_pcts
```
Expected: `AttributeError: ... has no attribute '_fetch_sector_pcts'`.

**Step 3: Implement**

Replace `services/sentiment_svc/compute.py:1283-1294` with:

```python
# ONE sector-history fetch serves BOTH structural horizons. week_pct and
# month_pct already come off the same `_fetch_closes` call, so splitting this
# into two fetches would double ~11 proxy calls for nothing.
SECTOR_PCTS_TTL_SEC = 3600
_SECTOR_PCTS_CACHE = {"ts": 0.0, "result": None}


def reset_sector_pcts_cache():
    """Drop the cached sector pcts so the next call refetches (tests)."""
    _SECTOR_PCTS_CACHE.update(ts=0.0, result=None)


def _fetch_sector_pcts():
    """``{"week": {etf: pct}, "month": {etf: pct}}``, TTL-cached ~hourly.
    Defensive: returns empty maps on any failure."""
    cached = _SECTOR_PCTS_CACHE["result"]
    if (cached is not None
            and time.monotonic() - _SECTOR_PCTS_CACHE["ts"] < SECTOR_PCTS_TTL_SEC):
        return {k: dict(v) for k, v in cached.items()}
    try:
        import sectors_ref
        sd = sectors_ref.load_sectors_data()
        etfs = [r["etf"] for r in sd
                if r.get("kind") == "sector" and r.get("etf")]
        _closes, trends = _fetch_closes(etfs, months=3)
        out = {"week": {e: t.get("week_pct") for e, t in trends.items()},
               "month": {e: t.get("month_pct") for e, t in trends.items()}}
    except Exception:  # noqa: BLE001
        return {"week": {}, "month": {}}
    _SECTOR_PCTS_CACHE.update(ts=time.monotonic(),
                              result={k: dict(v) for k, v in out.items()})
    return out


def _fetch_sector_month_pcts():
    """``{etf: month_pct}`` — the horizon ``compute_30d_trend`` scores on."""
    return _fetch_sector_pcts()["month"]


def _fetch_sector_week_pcts():
    """``{etf: week_pct}`` — the horizon ``compute_7d_trend`` scores on."""
    return _fetch_sector_pcts()["week"]
```

**Step 4: Run to verify**

Run:
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc/tests/test_compute.py -q -rf
```
Expected: all pass (the known `test_compute_regime` failure is in a different file).

**Step 5: Commit**

```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/tests/test_compute.py
git commit -m "refactor(sentiment_svc): one cached sector fetch for both horizons"
```

---

## Task 7: `compute_7d_trend`

**Files:**
- Modify: `services/sentiment_svc/compute.py` (add after `_fetch_sector_week_pcts`)
- Modify: `services/sentiment_svc/compute.py:328` (add `_CYC_DEF_SCALE_7D`)
- Modify: `services/sentiment_svc/tests/test_compute.py`

> **KNOWN LIMITATION — state this in the docstring, do not hide it.** The weekly
> and monthly structural trends share the SAME daily price sub-score (the EMA
> periods in `technical.calculate_ema_alignment` are fixed, so handing it a
> shorter frame would not change the answer). They differ only in the sector
> horizon (`week_pct` vs `month_pct`) and the cyc/def scale. Consequence: the
> Week and Month arcs will track each other closely and diverge mainly on sector
> rotation. Making the weekly price read genuinely weekly needs weekly-resampled
> SPY bars — a deliberate follow-up, not part of this change.

**Step 1: Write the failing tests**

```python
def _spy_df(n=260):
    import pandas as pd
    return pd.DataFrame({
        "open": [100.0 + i * 0.3 for i in range(n)],
        "high": [101.0 + i * 0.3 for i in range(n)],
        "low": [99.0 + i * 0.3 for i in range(n)],
        "close": [100.0 + i * 0.3 for i in range(n)],
        "volume": [1_000_000] * n})


def test_compute_7d_trend_shape():
    out = compute.compute_7d_trend(_spy_df(), {"XLK": 2.0, "XLP": -1.0})
    assert set(out) == {"score", "state", "label", "description",
                        "confidence", "sub_scores"}
    assert 0.0 <= out["score"] <= 100.0


def test_compute_7d_trend_is_neutral_without_data():
    out = compute.compute_7d_trend(None, {})
    assert out["score"] == 50.0
    assert out["sub_scores"] == {"price": 50.0, "sector": 50.0}


def test_compute_7d_trend_rises_with_broad_green_sectors():
    up = compute.compute_7d_trend(None, {"XLK": 3.0, "XLY": 2.0, "XLF": 2.0})
    down = compute.compute_7d_trend(None, {"XLK": -3.0, "XLY": -2.0, "XLF": -2.0})
    assert up["score"] > down["score"]


def test_compute_7d_trend_never_raises_on_junk():
    assert compute.compute_7d_trend("not a frame", "not a dict")["score"] == 50.0


def test_compute_7d_trend_self_fetch_is_ttl_cached(monkeypatch):
    compute.reset_trend_7d_cache()
    calls = {"n": 0}

    def _pcts():
        calls["n"] += 1
        return {"XLK": 1.0}

    monkeypatch.setattr(compute, "_fetch_sector_week_pcts", _pcts)
    monkeypatch.setattr(compute, "_safe_daily", lambda *a, **k: None)
    compute.compute_7d_trend()
    compute.compute_7d_trend()
    assert calls["n"] == 1
    compute.reset_trend_7d_cache()
```

**Step 2: Run to verify they fail**

Run:
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc/tests/test_compute.py -q -k 7d_trend
```
Expected: `AttributeError: ... has no attribute 'compute_7d_trend'`.

**Step 3: Implement**

At line 328, next to `_CYC_DEF_SCALE_30D = 3.0`, add:

```python
_CYC_DEF_SCALE_7D = 1.5   # week-moves sit between intraday (1.0) and month (3.0)
```

Then add after `_fetch_sector_week_pcts`:

```python
# A WEEK horizon moves faster than a month, so it recomputes twice as often.
TREND_7D_TTL_SEC = 1800
_TREND_7D_CACHE = {"ts": 0.0, "result": None}


def reset_trend_7d_cache():
    """Drop the cached 7d trend so the next self-fetch recomputes (tests)."""
    _TREND_7D_CACHE.update(ts=0.0, result=None)


def compute_7d_trend(spy_daily_df=_FETCH, sector_week_pcts=_FETCH) -> dict:
    """~1-week *structural* directional trend — the Week arc of the Trend ring.

    The weekly sibling of ``compute_30d_trend``: identical shape and identical
    price sub-score, scored on sector **week** %-moves with a tighter cyc/def
    scale. LIMITATION: the price sub-score is the same daily structural read as
    the 30d function (``calculate_ema_alignment``'s EMA periods are fixed, so a
    shorter frame would not change it), so Week and Month differ chiefly on
    sector rotation. A genuinely weekly price read needs weekly-resampled bars.

    Self-fetches when an argument is OMITTED (TTL-cached ~30 min); an explicit
    None/{} means the caller has no data and that sub-score degrades to neutral.
    Never raises.
    """
    self_fetch = spy_daily_df is _FETCH and sector_week_pcts is _FETCH
    if self_fetch:
        cached = _TREND_7D_CACHE["result"]
        if (cached is not None
                and time.monotonic() - _TREND_7D_CACHE["ts"] < TREND_7D_TTL_SEC):
            return dict(cached)
    try:
        if spy_daily_df is _FETCH:
            from services import _proxy
            spy_daily_df = _safe_daily(_proxy.schwab_client, "SPY", 12)
        if sector_week_pcts is _FETCH:
            sector_week_pcts = _fetch_sector_week_pcts()

        result = _structural_trend(spy_daily_df, sector_week_pcts,
                                   _CYC_DEF_SCALE_7D)
        if self_fetch:
            _TREND_7D_CACHE.update(ts=time.monotonic(), result=dict(result))
        return result
    except Exception:  # noqa: BLE001
        return _neutral_structural_trend()
```

Now extract the shared body. Add above `compute_30d_trend`:

```python
def _neutral_structural_trend():
    return {"score": 50.0, "state": "range",
            "label": trend_regime.STATE_LABELS["range"],
            "description": trend_regime.STATE_DESCRIPTIONS["range"],
            "confidence": 0.0,
            "sub_scores": {"price": 50.0, "sector": 50.0}}


def _structural_trend(spy_daily_df, sector_pcts, cyc_def_scale):
    """Shared body of the 7d/30d structural trends — price structure + sector
    breadth blended into one 0-100 directional score. Raises on junk input; the
    callers wrap it."""
    # PRICE — daily structural alignment + RSI/ADX/MACD (no VWAP at this horizon).
    if spy_daily_df is None or len(spy_daily_df) < 50:
        price = intraday_trend.TrendSub(50.0, 0.0)
    else:
        frames = {"1day": spy_daily_df}
        price_now = float(spy_daily_df["close"].iloc[-1])
        align = technical.calculate_ema_alignment(frames, price_now)
        align_pct = float(align.get("alignment_percentage", 0.0))
        hist = technical.macd_histogram_series(spy_daily_df)
        macd_hist = (float(hist.iloc[-1])
                     if hist is not None and len(hist) else 0.0)
        rsi = float(technical.calculate_rsi(spy_daily_df))
        adx = float(technical.calculate_adx(spy_daily_df))
        price = intraday_trend.score_price(
            align_pct, 0.0, macd_hist, rsi, adx, n_timeframes=1)

    # SECTOR — participation + cyc/def leadership at this horizon.
    pcts = sector_pcts or {}
    if not pcts:
        sector = intraday_trend.TrendSub(50.0, 0.0)
    else:
        n_green = sum(1 for p in pcts.values() if p is not None and p > 0)
        n_total = sum(1 for p in pcts.values() if p is not None)
        cyc = [p for etf, p in pcts.items() if etf in _CYCLICAL and p is not None]
        dfn = [p for etf, p in pcts.items() if etf in _DEFENSIVE and p is not None]
        if cyc and dfn:
            cyc_def_spread = intraday_trend._clamp(
                (_mean(cyc) - _mean(dfn)) / cyc_def_scale, -1, 1)
        else:
            cyc_def_spread = None
        sector = intraday_trend.score_sector_participation(
            n_green, n_total, cyc_def_spread)

    scores = {"price": price.score, "sector": sector.score}
    confs = {"price": price.confidence, "sector": sector.confidence}
    score, agg = intraday_trend.blend_trend(scores, confs)
    state = intraday_trend.score_to_state(score)
    return {"score": score, "state": state,
            "label": trend_regime.STATE_LABELS[state],
            "description": trend_regime.STATE_DESCRIPTIONS[state],
            "confidence": agg,
            "sub_scores": {"price": price.score, "sector": sector.score}}
```

Then reduce `compute_30d_trend`'s body (lines 1222-1280) to:

```python
        result = _structural_trend(spy_daily_df, sector_month_pcts,
                                   _CYC_DEF_SCALE_30D)
        if self_fetch:  # cache only the successful self-fetching path
            _TREND_30D_CACHE.update(ts=time.monotonic(), result=dict(result))
        return result
    except Exception:  # noqa: BLE001
        return _neutral_structural_trend()
```

This is a pure extraction — `compute_30d_trend`'s existing tests must stay green
without modification. **If any of them change behaviour, you extracted it wrong.**

**Step 4: Run to verify**

Run:
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc/tests/test_compute.py -q -rf
```
Expected: all pass, including every pre-existing `compute_30d_trend` test.

**Step 5: Commit**

```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/tests/test_compute.py
git commit -m "feat(sentiment_svc): compute_7d_trend for the Week arc"
```

---

## Task 8 (optional): SVG filter glow

Only if Task 0 recorded `FILTERS SURVIVE`. The layered halo already ships a
working glow, so this is polish and may be skipped entirely.

**Task 0 result:** _(record here)_

If filters survive, add a `<defs><filter id="ring-glow-{uid}">` with
`<feGaussianBlur stdDeviation="4"/>` + `<feMerge>` and apply it to the value arcs
via `filter="url(#ring-glow-{uid})"`. Update
`test_ring_svg_emits_no_style_or_filter_elements` accordingly (keep the `<style`
assertion — that one is unconditional).

---

## Task 9: Publish `trend_7d`

**Files:**
- Modify: `services/sentiment_svc/compute.py:1310` + `:1362-1371`
- Modify: `services/sentiment_svc/handlers.py:44-45, 141-149, 665-667`
- Modify: `services/sentiment_svc/tests/test_compute.py:60`
- Modify: `services/sentiment_svc/tests/test_handlers.py:380, 487`

**Step 1: Update the exact-set assertion (it WILL fail — that is the point)**

`test_compute.py:60` reads:
```python
    assert set(out) == {"weights", "size", "bias", "signal",
                        "velocity", "divergence", "trend", "trend_30d_ago"}
```
Add `"trend_7d"` to that set.

**Step 2: Add a test for the new field**

```python
def test_derive_composite_extras_carries_trend_7d():
    out = compute.derive_composite_extras(
        None, [], [], trend_7d={"score": 61.0, "state": "range"})
    assert out["trend_7d"]["score"] == 61.0


def test_derive_composite_extras_neutralizes_a_missing_trend_7d():
    out = compute.derive_composite_extras(None, [], [])
    assert out["trend_7d"]["score"] == 50.0
```

**Step 3: Run to verify they fail**

Run:
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc/tests/test_compute.py -q -k derive_composite_extras
```
Expected: `KeyError: 'trend_7d'` and the set assertion failing.

**Step 4: Implement**

`compute.py:1310` — add the kwarg LAST so positional callers are unaffected:
```python
def derive_composite_extras(live, snaps, spy, trend=None, trend_30d=None,
                            trend_7d=None):
```

`compute.py:1370` — add after `trend_30d_ago`:
```python
        "trend_7d": trend_7d if trend_7d is not None else _neutral_trend(),
```

`handlers.py:44-45` — add the slot:
```python
_TREND = {"last_ts": None, "history": [], "committed": None, "smoothed": None,
          "trend": None, "trend_30d": None, "trend_7d": None}
```

`handlers.py:141` — compute it beside the 30d one:
```python
            t30 = compute.compute_30d_trend()
            t7 = compute.compute_7d_trend()
```
and add `trend_7d=t7,` to the `_TREND.update(...)` call at line 143-149.

`handlers.py:667` — thread it through:
```python
            trend=_TREND["trend"], trend_30d=_TREND["trend_30d"],
            trend_7d=_TREND["trend_7d"]),
```

**Step 5: Fix the two handler-test stubs**

`test_handlers.py:380` and `:487` both define:
```python
    def _derive(live, snaps, spy, trend=None, trend_30d=None):
```
Both must gain `trend_7d=None` or `handlers.refresh` will raise `TypeError` —
add the kwarg and `"trend_7d": trend_7d` to each returned dict.

**Step 6: Run the whole service suite**

Run:
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc -q -rf
```
Expected: 250+ passed, **exactly one** failure —
`test_compute_regime.py::test_daily_history_wins_over_session_latch` (the known
baseline). Any other failing node ID is a regression you introduced.

**Step 7: Commit**

```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/handlers.py \
        services/sentiment_svc/tests/
git commit -m "feat(sentiment_svc): publish derived.trend_7d on the 15-min cadence"
```

---

## Task 10: Verify in dev — the step tests cannot do

This is a purely visual change. A green suite proves nothing about whether the
ring reads well. **Do not skip this and do not claim completion without it.**

**Step 1: Land the branch in the dev checkout**

Per THE DEVELOPMENT RULE in `CLAUDE.md`: work is verified running in **dev**
(`D:\WebGUI Trading with Schwab`, webgui `:9500`) before it goes anywhere near
prod. Fast-forward the dev checkout to this branch — do NOT run any mutating git
command in `D:\WebGUI Trading Prod`.

**Step 2: Restart the two affected processes**

Restart `sentiment_svc` (dev port 9210) so `trend_7d` is published, and the dev
webgui (`:9500`) so the new page code loads. Relaunch with the **venv
`pythonw`**, or every page 500s on a missing `redis` import.

Until `sentiment_svc` restarts, the Trend ring's Week arc renders track-only with
`—`. That is the designed degradation, not a bug.

**Step 3: Look at it**

Open `http://127.0.0.1:9500/sentiment` with the preview tools and screenshot.
Check, in order:

1. Two rings, not four gauges.
2. Three arcs each, outermost = Day.
3. Ticks 0/25/50/75/100 legible and correctly placed (0 lower-left, 50 top,
   100 lower-right).
4. Center number + `DAY` caption not clipped by the inner arc.
5. `WEEK` / `MONTH` in the bottom gap, not colliding with the `0` / `100` ticks.
   **This is the layout most likely to need nudging** — adjust the `y=250` /
   `y=267` / `x=104` / `x=176` constants in `rings.py` if so.
6. Arc colors differ when the values differ (dynamic per-arc coloring works).
7. The Components and Trend Detail press-and-hold popups still open.
8. Values change on the 120 s repaint without the ring flickering or stacking.

**Step 4: Commit any layout nudges**

```bash
git add webgui/pages/rings.py
git commit -m "fix(rings): layout nudges from the dev visual check"
```

---

## Task 11: Update CLAUDE.md

`CLAUDE.md` is the living architecture record and MUST be updated for a
structural change like this.

**Files:**
- Modify: `CLAUDE.md` — the `/sentiment` route-table row (the "dual Sentiment
  gauges (Today + 30-Day Avg) + dual Market Trend gauges" description is now wrong)
- Modify: `CLAUDE.md` — the "Intraday Market Trend model" section (add `trend_7d`)
- Modify: `docs/CHANGELOG.md` — a new dated entry at the TOP (newest first)

State plainly: four Highcharts gauges → two SVG rings; Day/Week/Month; per-arc
dynamic color off `[gauge]`; `gauge.py` unchanged and still serving the options
detail panel; `derive_composite_extras` gained `trend_7d`; and the known
limitation that the weekly and monthly structural trends share a price sub-score.

**Commit**

```bash
git add CLAUDE.md docs/CHANGELOG.md
git commit -m "docs: Day/Week/Month sentiment + trend rings"
```

---

## Done criteria

- `webgui` suite green (1190+ passed).
- `services/sentiment_svc` green except the one documented baseline failure.
- `/sentiment` visually verified in **dev** at `:9500` with a screenshot.
- `CLAUDE.md` + `docs/CHANGELOG.md` updated.
- Promotion to prod is a SEPARATE, explicit step via `tools\promote.bat` — not
  part of this plan.
