# Desk Bull / Bear Intraday Horizon — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Re-key the Desk's Bull / Bear sector strip so its colour and ordering follow **today's** price action, while the quarter-horizon reading survives as a left border stripe.

**Architecture:** `sentiment_svc` adds SPY to the existing single batched `/quotes` call and attaches `day_excess` per row. A new pure `day_quadrant` in `webgui/pages/bullbear.py` mirrors the existing `quadrant` exactly, so the two horizons differ only in horizon, never in rule. The Desk decides live-vs-structural from `shared.market_calendar` rather than inferring absence from zeros.

**Tech Stack:** Python 3.11, NiceGUI (Tier 1), FastAPI services (Tier 2), Redis bus, pytest.

**Design doc:** [`2026-09-05-desk-bullbear-intraday-design.md`](2026-09-05-desk-bullbear-intraday-design.md)

---

## Conventions for every task

Interpreter (this worktree has no venv of its own):

```
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
```

- **webgui tests** run from `webgui/`: `cd webgui && "$PY" -m pytest tests/<file> -q`
- **service tests** run from the repo root: `"$PY" -m pytest services/sentiment_svc/tests/<file> -q`
- **shared tests** run from the repo root: `"$PY" -m pytest shared/tests/<file> -q`
- Never run `pytest services` across all services — it re-triggers the documented `scoring`/`config`/`notifier` cross-app name collisions.
- Every commit message ends with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **Tier 1 may not import `services.*`.** The webgui's allow-list is `nicegui`, `shared.bus`, `shared.market_calendar`, `shared.symbols`, `shared.calibration`, `repo_paths`, `requests`, `fastapi.responses`. `shared.market_calendar` is on it — that is what Task 1 relies on.
- **No `.style()` anywhere in `webgui/pages`.** `test_no_inline_style.py` enforces it.

---

## Task 1: `regular_session_has_opened` in the shared calendar

The Desk must ask "has a regular session begun today?" without touching
`market_calendar._session_bounds`, which is private. `session_at` alone is not
enough: it returns `CLOSED` after 15:00 CT on a day with no active curb window,
and today's move is still real and fresh then.

**Files:**
- Modify: `shared/market_calendar.py` (add beside `is_regular_hours`, ~line 418)
- Test: `shared/tests/test_market_calendar.py`

**Step 1: Write the failing tests**

```python
from datetime import datetime
from shared import market_calendar as mc


def test_regular_session_has_opened_is_false_before_the_open():
    # 08:00 CT on a Tuesday, before the 08:30 open.
    assert mc.regular_session_has_opened(datetime(2026, 9, 8, 8, 0)) is False


def test_regular_session_has_opened_is_true_during_and_after_the_session():
    assert mc.regular_session_has_opened(datetime(2026, 9, 8, 9, 30)) is True
    # After the cash close the day's move is still real, so this stays True.
    assert mc.regular_session_has_opened(datetime(2026, 9, 8, 15, 45)) is True


def test_regular_session_has_opened_is_false_at_the_weekend():
    # 2026-09-05 is a Saturday.
    assert mc.regular_session_has_opened(datetime(2026, 9, 5, 12, 0)) is False
```

**Step 2: Run to verify they fail**

Run: `"$PY" -m pytest shared/tests/test_market_calendar.py -k regular_session_has_opened -q`
Expected: FAIL — `AttributeError: module 'shared.market_calendar' has no attribute 'regular_session_has_opened'`

**Step 3: Implement**

```python
def regular_session_has_opened(now) -> bool:
    """True on a trading day at or after the 08:30 CT open, close included.

    Distinct from :func:`is_regular_hours`, which goes False at 15:00 CT. A
    consumer asking "is today's move real yet?" wants True from the opening bell
    until the session date rolls -- the day's change does not stop being a fact
    at the close. False on weekends and holidays, where a quote's percent field
    is a stale prior close or the proxy's literal 0.0 fallback.
    """
    ct = _ct_of(now)
    if not is_trading_day(ct.date()):
        return False
    reg_start, _ = _session_bounds("regular")
    return ct.time() >= reg_start
```

**Step 4: Run to verify they pass**

Run: `"$PY" -m pytest shared/tests/test_market_calendar.py -q`
Expected: PASS, no previously-passing test broken.

**Step 5: Commit**

```bash
git add shared/market_calendar.py shared/tests/test_market_calendar.py
git commit -m "feat(calendar): regular_session_has_opened, open through end of day"
```

---

## Task 2: SPY joins the quote fan-out

**Files:**
- Modify: `services/sentiment_svc/compute.py` — `bullbear_symbols` (~2195)
- Test: `services/sentiment_svc/tests/test_bullbear.py`

**Step 1: Write the failing test**

```python
def test_bullbear_symbols_includes_the_benchmark_exactly_once():
    levels = {"sector": [{"symbol": "XLK"}], "industry": [], "stock": [{"symbol": "SPY"}]}
    out = compute.bullbear_symbols(levels)
    assert out.count("SPY") == 1          # already a row -> not added twice
    assert "XLK" in out

    out2 = compute.bullbear_symbols({"sector": [{"symbol": "XLK"}]})
    assert "SPY" in out2                  # not a row -> appended
```

**Step 2: Run to verify it fails**

Run: `"$PY" -m pytest services/sentiment_svc/tests/test_bullbear.py -k benchmark -q`
Expected: FAIL — `assert 'SPY' in ['XLK']`

**Step 3: Implement**

Add the module constant near `BULLBEAR_LEVELS`:

```python
# The Bull/Bear relative axis is measured against this, and the Desk strip's
# intraday quadrant needs its day move alongside the sectors'. It rides the
# SAME batched call -- 374 symbols came back in one request (measured
# 2026-08-19), so this is one more symbol, not one more request.
BULLBEAR_BENCHMARK = "SPY"
```

Then, at the end of `bullbear_symbols`, before `return out`:

```python
    if BULLBEAR_BENCHMARK not in seen:
        out.append(BULLBEAR_BENCHMARK)
    return out
```

**Step 4: Run to verify it passes**

Run: `"$PY" -m pytest services/sentiment_svc/tests/test_bullbear.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/tests/test_bullbear.py
git commit -m "feat(sentiment_svc): quote SPY alongside the bullbear tree"
```

---

## Task 3: `day_excess` and `benchmark_day_pct`

**Files:**
- Modify: `services/sentiment_svc/compute.py` — `merge_live` (~2211), `bullbear_view` (~2249)
- Test: `services/sentiment_svc/tests/test_bullbear.py`

**Step 1: Write the failing tests**

```python
def test_merge_live_attaches_day_excess_against_the_benchmark():
    levels = {"sector": [{"symbol": "XLK"}], "industry": [], "stock": []}
    quotes = {"XLK": {"change_pct": 1.25}, "SPY": {"change_pct": 0.25}}
    row = compute.merge_live(levels, quotes)["sector"][0]
    assert row["day_pct"] == 1.25
    assert row["day_excess"] == pytest.approx(1.0)


def test_day_excess_is_none_when_either_side_is_missing():
    levels = {"sector": [{"symbol": "XLK"}, {"symbol": "XLF"}],
              "industry": [], "stock": []}
    # SPY omitted entirely -> no relative axis for anyone.
    rows = compute.merge_live(levels, {"XLK": {"change_pct": 1.0}})["sector"]
    assert rows[0]["day_excess"] is None
    # A symbol the proxy omitted -> None, NEVER 0.0, which would read "in line".
    rows = compute.merge_live(levels, {"SPY": {"change_pct": 0.5}})["sector"]
    assert rows[0]["day_pct"] is None and rows[0]["day_excess"] is None
```

**Step 2: Run to verify they fail**

Run: `"$PY" -m pytest services/sentiment_svc/tests/test_bullbear.py -k day_excess -q`
Expected: FAIL — `KeyError: 'day_excess'`

**Step 3: Implement**

In `merge_live`, before the level loop:

```python
    bench = ((quotes or {}).get(BULLBEAR_BENCHMARK) or {}).get("change_pct")
    bench = float(bench) if isinstance(bench, (int, float)) else None
```

and inside the row loop, after `out_row["day_pct"] = ...`:

```python
        # None when EITHER side is absent. 0.0 would say "moved exactly with
        # SPY", a claim nobody measured -- the same distinction day_pct keeps.
        day = out_row["day_pct"]
        out_row["day_excess"] = (day - bench) if (day is not None and bench is not None) else None
```

In `bullbear_view`, where the payload dict is assembled, add:

```python
        "benchmark_day_pct": bench_pct,      # None when the proxy omitted SPY
```

reading it back from the merged quotes the same way (extract once into a local
in `bullbear_view` and pass it down, or re-read from `quotes`).

**Step 4: Run to verify they pass**

Run: `"$PY" -m pytest services/sentiment_svc/tests/test_bullbear.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/tests/test_bullbear.py
git commit -m "feat(sentiment_svc): day_excess vs SPY on every bullbear row"
```

---

## Task 4: `day_quadrant` — the intraday classifier

**Files:**
- Modify: `webgui/pages/bullbear.py` (add after `row_axes`, ~line 123)
- Test: `webgui/tests/test_bullbear.py`

**Step 1: Write the failing tests**

```python
def test_day_quadrant_matches_the_structural_rule_exactly():
    # Same rule, different horizon: identical inputs must classify identically,
    # so any strip/map disagreement is a horizon difference and never a rule one.
    for t in (-1.0, -0.01, 0.0, 0.01, 1.0):
        for e in (-1.0, -0.01, 0.0, 0.01, 1.0):
            assert B.day_quadrant(t, e) == B.quadrant(t, e)


def test_day_quadrant_is_unknown_when_either_axis_is_missing():
    assert B.day_quadrant(None, 0.4) == "unknown"
    assert B.day_quadrant(0.4, None) == "unknown"
    assert B.day_quadrant(float("nan"), 0.4) == "unknown"


def test_row_day_axes_reads_the_top_level_live_fields():
    row = {"day_pct": 1.2, "day_excess": 0.3, "raw": {"trend": 9.0, "excess": 9.0}}
    assert B.row_day_axes(row) == (1.2, 0.3)
```

**Step 2: Run to verify they fail**

Run: `cd webgui && "$PY" -m pytest tests/test_bullbear.py -k day_quadrant -q`
Expected: FAIL — `AttributeError: module 'pages.bullbear' has no attribute 'day_quadrant'`

**Step 3: Implement**

```python
def row_day_axes(row):
    """A row's ``(day_pct, day_excess)`` -- day_quadrant()'s input.

    These live at the TOP level, not under ``raw``: ``raw`` is the nightly
    cascade's own output and ``merge_live`` deliberately copies rather than
    writes into it.
    """
    row = row or {}
    return _num(row.get("day_pct")), _num(row.get("day_excess"))


def day_quadrant(day_pct, day_excess):
    """Today's move x today's move vs SPY -> one of QUADRANTS.

    Deliberately delegates to :func:`quadrant`: the two horizons MUST classify
    identically, tie rule included, or a strip/map disagreement stops being
    readable as a horizon difference. No deadband -- a sector genuinely
    oscillating around SPY's return is shown oscillating, which is true.
    """
    return quadrant(day_pct, day_excess)
```

**Step 4: Run to verify they pass**

Run: `cd webgui && "$PY" -m pytest tests/test_bullbear.py -q`
Expected: PASS (47 existing + new)

**Step 5: Commit**

```bash
git add webgui/pages/bullbear.py webgui/tests/test_bullbear.py
git commit -m "feat(bullbear): day_quadrant, the intraday twin of quadrant"
```

---

## Task 5: day-ranked ordering with hysteresis

**Files:**
- Modify: `webgui/pages/bullbear.py` (add after `by_strength`, ~line 220)
- Test: `webgui/tests/test_bullbear.py`

Bucket-quantised rather than a pairwise state machine: a chip moves only when it
crosses a margin-sized bucket boundary, and inside a bucket it keeps its previous
position. Pure, deterministic, and testable without a browser.

**Step 1: Write the failing tests**

```python
def test_by_day_move_orders_strongest_first():
    rows = [{"symbol": "A", "day_pct": 0.2}, {"symbol": "B", "day_pct": 1.4}]
    assert [r["symbol"] for r in B.by_day_move(rows)] == ["B", "A"]


def test_by_day_move_holds_position_inside_the_margin():
    # B leads A by 0.01pp -- inside DAY_SORT_MARGIN_PCT, so the previous order
    # survives rather than the strip reshuffling on noise.
    rows = [{"symbol": "A", "day_pct": 1.00}, {"symbol": "B", "day_pct": 1.01}]
    out = B.by_day_move(rows, previous=["A", "B"])
    assert [r["symbol"] for r in out] == ["A", "B"]


def test_by_day_move_reorders_once_the_margin_is_cleared():
    rows = [{"symbol": "A", "day_pct": 1.00}, {"symbol": "B", "day_pct": 1.40}]
    out = B.by_day_move(rows, previous=["A", "B"])
    assert [r["symbol"] for r in out] == ["B", "A"]


def test_by_day_move_puts_unreadable_rows_last():
    rows = [{"symbol": "A", "day_pct": None}, {"symbol": "B", "day_pct": -2.0}]
    assert [r["symbol"] for r in B.by_day_move(rows)] == ["B", "A"]
```

**Step 2: Run to verify they fail**

Run: `cd webgui && "$PY" -m pytest tests/test_bullbear.py -k by_day_move -q`
Expected: FAIL — `AttributeError: ... 'by_day_move'`

**Step 3: Implement**

```python
# Sort bucket width, in percentage points. A strip that repaints every ~30 s and
# re-sorts on any difference reshuffles on sub-basis-point noise, and a strip
# that moves under the eye is not glanceable. 0.05pp is well inside the move a
# reader would call a difference between two sectors.
DAY_SORT_MARGIN_PCT = 0.05


def by_day_move(rows, previous=None, margin=DAY_SORT_MARGIN_PCT):
    """Rows by today's move, strongest first, quantised so small moves hold place.

    ``previous`` is the symbol order from the last paint; inside one bucket that
    order is preserved, so a chip changes position only when it crosses a bucket
    boundary. Rows with no readable move sort last -- ``None`` is "the proxy
    omitted this symbol", which is not a reason to call it the weakest.
    """
    order = {s: i for i, s in enumerate(previous or [])}

    def key(row):
        pct = _num((row or {}).get("day_pct"))
        seat = order.get((row or {}).get("symbol"), len(order))
        if pct is None:
            return (1, 0, seat)
        return (0, -int(pct / margin), seat)

    return sorted((r for r in rows or [] if r is not None), key=key)
```

**Step 4: Run to verify they pass**

Run: `cd webgui && "$PY" -m pytest tests/test_bullbear.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/pages/bullbear.py webgui/tests/test_bullbear.py
git commit -m "feat(bullbear): by_day_move, day-ranked order with hysteresis"
```

---

## Task 6: the strip's live/structural switch

**Files:**
- Modify: `webgui/pages/desk.py` — near `_bullbear_rows`/`bullbear_chips` (~691-745)
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

```python
def test_strip_is_not_live_outside_a_session():
    view = {"levels": {"sector": []}, "benchmark_day_pct": 0.4}
    assert desk.strip_is_live(view, now=datetime(2026, 9, 5, 12, 0)) is False  # Saturday


def test_strip_is_not_live_when_the_benchmark_is_missing():
    view = {"levels": {"sector": []}, "benchmark_day_pct": None}
    assert desk.strip_is_live(view, now=datetime(2026, 9, 8, 10, 0)) is False


def test_strip_is_live_during_a_session_with_a_benchmark():
    view = {"levels": {"sector": []}, "benchmark_day_pct": 0.4}
    assert desk.strip_is_live(view, now=datetime(2026, 9, 8, 10, 0)) is True
```

**Step 2: Run to verify they fail**

Run: `cd webgui && "$PY" -m pytest tests/test_desk.py -k strip_is_live -q`
Expected: FAIL — `AttributeError: ... 'strip_is_live'`

**Step 3: Implement**

```python
from shared import market_calendar as _cal   # on the Tier-1 allow-list


def strip_is_live(bullbear_view, now=None):
    """Should the chips paint TODAY's quadrant rather than the structural one?

    Asks the calendar rather than inferring absence from zeros.
    ``SchwabProxyClient._extract_change_pct`` falls through to a literal ``0.0``
    when every percent field is missing, and ``0.0`` is not ``> 0`` -- so a
    naive test would paint all eleven sectors "Falling . Lagging" every
    pre-open, every weekend and through any proxy hiccup: a confident,
    maximally bearish reading of nothing.

    The benchmark clause catches the case the calendar cannot see -- a dead
    proxy mid-session, where ``merge_live`` leaves ``benchmark_day_pct`` None.
    """
    view = bullbear_view if isinstance(bullbear_view, dict) else {}
    if _num(view.get("benchmark_day_pct")) is None:
        return False
    return _cal.regular_session_has_opened(now or datetime.now())
```

**Step 4: Run to verify they pass**

Run: `cd webgui && "$PY" -m pytest tests/test_desk.py -k strip_is_live -q`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): decide the strip's horizon from the calendar, not zeros"
```

---

## Task 7: chips carry both quadrants

**Files:**
- Modify: `webgui/pages/desk.py` — `_bullbear_rows` (~691), `bullbear_chips` (~708)
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

```python
LIVE_ROW = {"symbol": "XLK", "label": "Information Technology",
            "day_pct": 1.2, "day_excess": 0.4,
            "raw": {"trend": -0.5, "excess": -0.2}, "participation": 0.8}


def test_chip_colour_follows_today_and_keeps_structure_as_a_stripe():
    view = {"levels": {"sector": [LIVE_ROW]}, "benchmark_day_pct": 0.8}
    chip = desk.bullbear_chips(view, now=datetime(2026, 9, 8, 10, 0))[0]
    assert chip["quadrant"] == "rising_leading"          # today
    assert chip["structural_quadrant"] == "falling_lagging"   # the quarter
    assert chip["live"] is True


def test_a_cold_tape_never_paints_the_whole_strip_bearish():
    """The producer-shaped no-data case: a payload with no day moves at all.

    Driven from the shape the SERVICE writes, because a consumer-side guard
    proves nothing until a test drives it from the producer.
    """
    rows = [{"symbol": s, "label": s, "day_pct": None, "day_excess": None,
             "raw": {"trend": 0.4, "excess": 0.1}} for s in ("XLK", "XLF", "XLV")]
    view = {"levels": {"sector": rows}, "benchmark_day_pct": None}
    chips = desk.bullbear_chips(view, now=datetime(2026, 9, 5, 12, 0))
    assert [c["quadrant"] for c in chips] == ["rising_leading"] * 3
    assert all(c["live"] is False for c in chips)
```

**Step 2: Run to verify they fail**

Run: `cd webgui && "$PY" -m pytest tests/test_desk.py -k "chip_colour or cold_tape" -q`
Expected: FAIL — `KeyError: 'structural_quadrant'`

**Step 3: Implement**

`_bullbear_rows(view, live, previous)` orders with `_bb.by_day_move` when live
and `_bb.by_strength` otherwise. `bullbear_chips(view, now=None, previous=None)`
gains, per chip:

```python
            "quadrant": (_bb.day_quadrant(*_bb.row_day_axes(row)) if live
                         else _bb.quadrant(*_bb.row_axes(row))),
            # Rendered only when live: off-session the two are the same value,
            # and a stripe repeating the fill says nothing.
            "structural_quadrant": _bb.quadrant(*_bb.row_axes(row)),
            "live": live,
```

**Step 4: Run to verify they pass**

Run: `cd webgui && "$PY" -m pytest tests/test_desk.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): bullbear chips carry today's quadrant and the structural one"
```

---

## Task 8: render the stripe, the sort label and the horizon headline

**Files:**
- Modify: `webgui/pages/desk.py` — `_BB_CHIP` (~2119), `_paint_bullbear` (~2788), `_bullbear_chip` (~2800), `bullbear_headline` (~746)
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

```python
def test_stripe_class_is_a_static_map_over_the_finite_quadrant_set():
    for q in B.QUADRANTS:
        assert desk.stripe_class(q)          # non-empty for every quadrant
    assert desk.stripe_class("nonsense") == desk.stripe_class("unknown")


def test_headline_names_the_horizon():
    live = {"levels": {"sector": [LIVE_ROW]}, "benchmark_day_pct": 0.8}
    assert desk.bullbear_headline(live, now=datetime(2026, 9, 8, 10, 0)).endswith("today")
    cold = {"levels": {"sector": [LIVE_ROW]}, "benchmark_day_pct": None}
    assert desk.bullbear_headline(cold, now=datetime(2026, 9, 5, 12, 0)).endswith("on the quarter")
```

**Step 2: Run to verify they fail**

Run: `cd webgui && "$PY" -m pytest tests/test_desk.py -k "stripe_class or names_the_horizon" -q`
Expected: FAIL — `AttributeError: ... 'stripe_class'`

**Step 3: Implement**

Add `border-l-[3px]` to `_BB_CHIP` and a static stripe palette keyed on the same
finite quadrant set (`border-l-emerald-400/70`, `.../40`, `border-l-amber-400/70`,
`border-l-rose-400/70`, `border-l-slate-400/50`). In `_bullbear_chip`, append the
stripe class only when `chip["live"]`, and put the structural quadrant's words in
the chip tooltip so colour is never the sole carrier. In `_paint_bullbear`, hold
the previous symbol order in the page's local state dict and pass it to
`bullbear_chips`; render a small "by today's move" caption when live and
"structural — no move yet" when not.

⚠ Keep the classes static and mapped from the finite set — no runtime-built
`text-[...]`, per the Tailwind-first standard.

**Step 4: Run to verify they pass**

Run: `cd webgui && "$PY" -m pytest tests/ -q`
Expected: PASS, including `test_no_inline_style.py`.

**Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): paint the intraday strip with a structural border stripe"
```

---

## Task 9: docs

**Files:**
- Modify: `docs/CHANGELOG.md`, `docs/webgui-routes.md`, `webgui/page_help.py`

`page_help.py` is the fifth manual and the most-read prose in the app; it rots
first. The Desk's help text currently describes a strip whose colour is the
nightly cascade — that sentence becomes wrong in Task 8 and must move in the same
change. Add a CHANGELOG entry and update the `/desk` section of
`docs/webgui-routes.md`. Do **not** add a feature narrative to CLAUDE.md.

**Commit**

```bash
git add docs/CHANGELOG.md docs/webgui-routes.md webgui/page_help.py
git commit -m "docs: the Desk strip's intraday horizon"
```

---

## Final verification

```bash
cd webgui && "$PY" -m pytest -q                          # full webgui suite
"$PY" -m pytest services/sentiment_svc -q                # 325 passed / 1 xfail baseline
"$PY" -m pytest shared/tests -q
```

Compare the failing **set**, never the count. Then verify in a running dev stack
before promoting — "tests pass" is not "verified in dev" for anything with a
runtime surface, and this feature's whole risk surface is what the strip paints
at 08:29 versus 08:31.
