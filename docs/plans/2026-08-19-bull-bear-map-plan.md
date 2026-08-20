# Bull / Bear Map Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A `/sentiment/bullbear` tab showing where the market is bullish and bearish across sectors → industries → stocks, with absolute trend and relative strength shown as separate marks plus a live day-move, and an 11-chip sector strip on the Desk.

**Architecture:** Tier-2 (`sentiment_svc`) merges the nightly momentum cascade with one batched live `/quotes` call and publishes ONE view, `cache:sentiment:bullbear`. Tier-1 renders it as a lazily-expanding tree. All display arithmetic lives in a pure module (`webgui/pages/bullbear.py`) tested without a browser; the page module (`webgui/pages/sentiment_bullbear.py`) is widgets and wiring only — the pattern the 2026-08-17 sentiment rebuilds established (`sector_heat.py` / `sentiment_sectors.py`).

**Tech Stack:** Python 3.11, NiceGUI (Tailwind-first, no Highcharts), Redis via `shared.bus`, pytest. Design: [`2026-08-19-bull-bear-map-design.md`](2026-08-19-bull-bear-map-design.md).

---

## Ground rules

- **Run suites per folder from the repo root.** Never `pytest services` over all of them — it re-triggers the documented `config`/`scoring` module-name collisions.
- **The venv is at the repo root**, not in a worktree: `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`.
- **Tailwind-first is mandatory.** No `.style()`, no `:style=`. A data-driven colour maps from a finite set to a static class via a pure lookup. Add the new page to `webgui/tests/test_no_inline_style.py`.
- **Baseline failures**: options-scanner has 11, sentiment_svc has 1 (`test_daily_history_wins_over_session_latch`). Compare the failing **set**, never the count.
- Commit after every task, conventional prefixes.

---

## Task 1: Classify one row into a quadrant

**Files:**
- Create: `webgui/pages/bullbear.py`
- Test: `webgui/tests/test_bullbear.py`

**Step 1: Write the failing test**

```python
"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, never blended: absolute trend (raw.trend) and relative strength
(raw.excess). See docs/plans/2026-08-19-bull-bear-map-design.md.
"""
from pages import bullbear as B


def test_quadrant_names_the_four_states():
    assert B.quadrant(0.5, 0.1) == "rising_leading"
    assert B.quadrant(0.5, -0.1) == "rising_lagging"
    assert B.quadrant(-0.5, 0.1) == "falling_leading"
    assert B.quadrant(-0.5, -0.1) == "falling_lagging"


def test_quadrant_treats_exact_zero_as_the_bearish_side():
    """A flat trend is not a rising one. Ties go to the cautious reading, so a
    dead-flat row never renders as strength."""
    assert B.quadrant(0.0, 0.1) == "falling_leading"
    assert B.quadrant(0.5, 0.0) == "rising_lagging"


def test_quadrant_is_unknown_when_either_axis_is_missing():
    """A thin or newly-listed symbol scores None. It must not default into a
    bucket — an invented reading is worse than an absent one."""
    assert B.quadrant(None, 0.1) == "unknown"
    assert B.quadrant(0.5, None) == "unknown"
    assert B.quadrant(None, None) == "unknown"
```

**Step 2: Run test to verify it fails**

Run: `cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_bullbear.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pages.bullbear'`

**Step 3: Write minimal implementation**

```python
"""Pure display language for the Bull / Bear Map (/sentiment/bullbear).

Two axes, NEVER blended. ``raw.trend`` is the annualised exp-regression slope of
log(close) scaled by R^2 — signed, absolute, benchmark-free. ``raw.excess`` is
excess return vs SPY — signed, relative. Their four combinations are the map, and
the fourth (falling but leading) is precisely what a relative-only screen paints
bullish. See docs/plans/2026-08-19-bull-bear-map-design.md.
"""

QUADRANTS = ("rising_leading", "rising_lagging",
             "falling_leading", "falling_lagging", "unknown")


def quadrant(trend, excess):
    """Absolute trend x relative strength -> one of QUADRANTS.

    Ties go to the cautious side: a dead-flat trend is not "rising", and a zero
    excess is not "leading". A missing axis yields ``unknown`` rather than a
    default bucket — the cascade returns None for a series too short or too thin
    to score, and inventing a reading there is worse than showing none.
    """
    if trend is None or excess is None:
        return "unknown"
    if trend > 0:
        return "rising_leading" if excess > 0 else "rising_lagging"
    return "falling_leading" if excess > 0 else "falling_lagging"
```

**Step 4: Run test to verify it passes**

Run: `cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_bullbear.py -q`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add webgui/pages/bullbear.py webgui/tests/test_bullbear.py
git commit -m "feat(bullbear): classify a row into one of four quadrants"
```

---

## Task 2: Quadrant labels and the finite colour vocabulary

**Files:**
- Modify: `webgui/pages/bullbear.py`
- Test: `webgui/tests/test_bullbear.py`

**Step 1: Write the failing test**

```python
def test_every_quadrant_has_a_label_and_a_class():
    for q in B.QUADRANTS:
        assert B.quadrant_label(q), f"{q} has no label"
        assert B.quadrant_class(q), f"{q} has no class"


def test_quadrant_labels_say_both_axes():
    """The label must name what it measures, because 'bullish' alone is the
    ambiguity this whole page exists to remove."""
    assert B.quadrant_label("rising_leading") == "Rising · Leading"
    assert B.quadrant_label("falling_leading") == "Falling · Leading"
    assert B.quadrant_label("unknown") == "No reading"


def test_quadrant_classes_are_a_finite_deduped_static_vocabulary():
    """Tailwind-first: a data-driven colour maps to a STATIC class from a fixed
    set, never a runtime-built arbitrary value."""
    classes = {q: B.quadrant_class(q) for q in B.QUADRANTS}
    assert len(set(classes.values())) == len(B.QUADRANTS)   # no two share a colour
    for value in classes.values():
        assert "{" not in value and "}" not in value        # not an f-string hole


def test_unknown_is_the_only_quadrant_without_direction_colour():
    """A no-reading row must not borrow green or red."""
    assert "emerald" not in B.quadrant_class("unknown")
    assert "rose" not in B.quadrant_class("unknown")
```

**Step 2: Run to verify it fails**

Run: `cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_bullbear.py -q`
Expected: FAIL — `AttributeError: module 'pages.bullbear' has no attribute 'quadrant_label'`

**Step 3: Implement**

```python
# Display language. The label names BOTH axes because "bullish" on its own is the
# ambiguity this page exists to remove.
_LABELS = {
    "rising_leading": "Rising · Leading",
    "rising_lagging": "Rising · Lagging",
    "falling_leading": "Falling · Leading",
    "falling_lagging": "Falling · Lagging",
    "unknown": "No reading",
}

# A FIXED finite palette of static Tailwind classes (house standard: never build
# a colour class at runtime). Strength green -> weakness rose, with the two mixed
# states in between, and slate for no-reading so it cannot be mistaken for a call.
_CLASSES = {
    "rising_leading": "text-emerald-300 bg-emerald-400/15 border-emerald-400/30",
    "rising_lagging": "text-emerald-200/80 bg-emerald-400/5 border-emerald-400/15",
    "falling_leading": "text-amber-300 bg-amber-400/10 border-amber-400/25",
    "falling_lagging": "text-rose-300 bg-rose-400/15 border-rose-400/30",
    "unknown": "text-slate-400 bg-slate-400/10 border-slate-400/20",
}


def quadrant_label(q):
    return _LABELS.get(q, _LABELS["unknown"])


def quadrant_class(q):
    return _CLASSES.get(q, _CLASSES["unknown"])
```

**Step 4: Run to verify it passes**

Expected: PASS (7 passed)

**Step 5: Commit**

```bash
git add webgui/pages/bullbear.py webgui/tests/test_bullbear.py
git commit -m "feat(bullbear): quadrant labels and a finite static colour vocabulary"
```

---

## Task 3: Count the quadrants (the page's headline)

**Files:**
- Modify: `webgui/pages/bullbear.py`
- Test: `webgui/tests/test_bullbear.py`

The headline is **counts, not a regime word** — see the design's "deliberate omission". CLAUDE.md documents `/sentiment/sectors` and `/sentiment/rotation` already printing opposite verdicts; this page must not add a third.

**Step 1: Write the failing test**

```python
def _row(trend, excess, **kw):
    row = {"symbol": kw.get("symbol", "X"), "label": kw.get("label", "X"),
           "raw": {"trend": trend, "excess": excess}}
    row.update({k: v for k, v in kw.items() if k not in ("symbol", "label")})
    return row


def test_quadrant_counts_bucket_every_row_exactly_once():
    rows = [_row(1.0, 0.1), _row(1.0, -0.1), _row(-1.0, 0.1),
            _row(-1.0, -0.1), _row(None, None)]
    counts = B.quadrant_counts(rows)
    assert counts == {"rising_leading": 1, "rising_lagging": 1,
                      "falling_leading": 1, "falling_lagging": 1, "unknown": 1}
    assert sum(counts.values()) == len(rows)


def test_quadrant_counts_report_every_bucket_even_when_empty():
    """A missing key would make the headline read '3 of 11' with no denominator
    context; every bucket is always present, at zero."""
    counts = B.quadrant_counts([_row(1.0, 0.1)])
    assert set(counts) == set(B.QUADRANTS)
    assert counts["falling_lagging"] == 0


def test_quadrant_counts_of_nothing_is_all_zero():
    assert B.quadrant_counts([]) == {q: 0 for q in B.QUADRANTS}
    assert B.quadrant_counts(None) == {q: 0 for q in B.QUADRANTS}


def test_headline_states_a_count_not_a_regime_word():
    """Guard for the design decision. /sentiment/sectors and /sentiment/rotation
    already print contradictory risk-on/risk-off verdicts from incommensurable
    quantities; this page reports arithmetic instead."""
    text = B.headline(B.quadrant_counts([_row(1.0, 0.1), _row(1.0, 0.1),
                                         _row(-1.0, -0.1)]), "sectors")
    assert "2 of 3" in text and "sectors" in text
    for banned in ("risk-on", "risk-off", "bullish regime", "bearish regime"):
        assert banned.lower() not in text.lower()
```

**Step 2: Run to verify it fails**

Expected: FAIL — no attribute `quadrant_counts`

**Step 3: Implement**

```python
def quadrant_counts(rows):
    """{quadrant: n} over rows, every bucket present even at zero.

    Always reporting all five keeps the headline's denominator honest and lets a
    caller render a full distribution without guarding each lookup.
    """
    counts = {q: 0 for q in QUADRANTS}
    for row in rows or []:
        raw = (row or {}).get("raw") or {}
        counts[quadrant(raw.get("trend"), raw.get("excess"))] += 1
    return counts


def headline(counts, noun):
    """'5 of 11 sectors rising and leading' — a FACT about rows on screen.

    Deliberately NOT a regime label. /sentiment/sectors and /sentiment/rotation
    each render a risk-on/risk-off verdict from a different, non-commensurable
    quantity and have been measured printing OPPOSITE words on the same day. A
    count cannot contradict another screen because it is arithmetic, not
    interpretation.
    """
    total = sum(counts.values())
    return f"{counts.get('rising_leading', 0)} of {total} {noun} rising and leading"
```

**Step 4: Run to verify it passes**

Expected: PASS (11 passed)

**Step 5: Commit**

```bash
git add webgui/pages/bullbear.py webgui/tests/test_bullbear.py
git commit -m "feat(bullbear): quadrant counts and a count-based headline"
```

---

## Task 4: Participation breadth bar

**Files:**
- Modify: `webgui/pages/bullbear.py`
- Test: `webgui/tests/test_bullbear.py`

Participation is what separates Energy (flat, 0.96 participating) from Real Estate (rising, 0.23). Sector and industry rows carry it; **stock rows carry `None`**.

**Step 1: Write the failing test**

```python
def test_breadth_width_is_a_percentage_of_the_bar():
    assert B.breadth_width(0.0) == 0
    assert B.breadth_width(0.5) == 50
    assert B.breadth_width(1.0) == 100


def test_breadth_width_rounds_to_whole_percent_and_clamps():
    assert B.breadth_width(0.9623) == 96
    assert B.breadth_width(1.4) == 100      # never overflow the track
    assert B.breadth_width(-0.2) == 0


def test_breadth_width_is_none_when_participation_is_absent():
    """Stock rows have no participation — a single name has no constituents. It
    must render nothing, not a zero-width bar that reads as 'no breadth'."""
    assert B.breadth_width(None) is None
    assert B.breadth_width("x") is None


def test_breadth_is_flagged_thin_below_the_third():
    """A sector rising on under a third of its members is a fragile advance and
    the map must say so rather than paint it the same green as a broad one."""
    assert B.breadth_is_thin(0.23) is True
    assert B.breadth_is_thin(0.33) is True
    assert B.breadth_is_thin(0.34) is False
    assert B.breadth_is_thin(None) is False
```

**Step 2: Run to verify it fails**

Expected: FAIL — no attribute `breadth_width`

**Step 3: Implement**

```python
# Below this share of confirming constituents, a move is called thin. One third
# is a judgement, not a fitted number: measured 2026-08-19, Real Estate was
# RISING on 0.23 participation while Energy was flat on 0.96 — the map has to
# separate those, and a third is where the live spread naturally splits.
THIN_PARTICIPATION = 1.0 / 3.0


def breadth_width(participation):
    """0..100 whole-percent bar width, or None when there is nothing to show.

    None (not 0) for a stock row: one name has no constituents, and a zero-width
    bar would read as 'no breadth' rather than 'not applicable'.
    """
    p = _num(participation)
    return None if p is None else int(round(max(0.0, min(1.0, p)) * 100))


def breadth_is_thin(participation):
    """True when a move is carried by too few constituents to trust."""
    p = _num(participation)
    return p is not None and p <= THIN_PARTICIPATION
```

> **Reuse `_num` from Task 1 — do not re-implement the numeric guard.** An earlier
> draft of this task inlined `not isinstance(p, (int, float)) or isinstance(p, bool)`
> twice, which would have forked the house idiom two tasks after Task 1 converged on
> it. `_num` already rejects `None`, bools and non-finite values and coerces the rest,
> which is exactly the test both functions need.

**Step 4: Run to verify it passes**

Expected: PASS (15 passed)

**Step 5: Commit**

```bash
git add webgui/pages/bullbear.py webgui/tests/test_bullbear.py
git commit -m "feat(bullbear): participation breadth bar with a thin-move flag"
```

---

## Task 5: Build the tree (sector → industry → stock)

> **⚠ Correction to an earlier draft of this task, verified at the source twice.**
> This plan originally attributed `orphan_stocks` to the four duplicate-ETF
> industries (MJ, XRT, BETZ, VEGI). **That mechanism cannot produce an orphan
> stock.** `_momentum_universe:1838` puts those industries in `orphans`, *not* in
> `universe["industries"]`, and `industry_of:2106` is built only from the latter —
> so a stock whose sole industry is a duplicate-ETF orphan resolves to `("", "")`
> and is dropped entirely, appearing in no row and no count. The real and common
> source is the admission gate, described in `build_tree`'s docstring below.
>
> Recorded because the wrong version was written into a test docstring on this
> plan's authority, and was caught only by reading the producer.

> ### ⚠ Two participation traps, found by the Task 4 review — read before writing
>
> **1. `participation` is at the row TOP LEVEL, not inside `raw`.** `quadrant_counts`
> reaches trend/excess through `_raw(row)`, but `row["participation"]` is a sibling of
> `raw`, not a child (confirmed at `services/sentiment_svc/compute.py:1993-1995` — it is
> *not* duplicated into `raw`). `_raw` tolerates a `None` row; a bare
> `row.get("participation")` on a `None` row raises `AttributeError`. The tree walks rows
> from three levels, so give participation its own accessor with the same tolerance
> rather than letting each call site improvise.
>
> **2. `participation` names TWO different quantities in the same payload, and the wrong
> one fails silently.** `row["participation"]` is the raw 0..1 share. But
> `row["components"]["participation"]` is a **within-level z-score**
> (`compute.py:1976-1988`) — signed and unbounded. Feed that to `breadth_width` and
> `_share` returns `None` for every negative value and a plausible-but-wrong percentage
> for anything that happens to land in [0, 1]. No exception, no empty render, just a
> quietly wrong bar. Name this trap in the accessor's docstring.


**Files:**
- Modify: `webgui/pages/bullbear.py`
- Test: `webgui/tests/test_bullbear.py`

Momentum rows already carry parent links: industry rows have `sector`, stock rows have `sector` **and** `industry`.

**Step 1: Write the failing test**

```python
def test_build_tree_nests_industries_and_stocks_under_their_parents():
    levels = {
        "sector": [_row(1.0, 0.1, symbol="XLV", label="Health Care")],
        "industry": [_row(1.0, 0.2, symbol="XBI", label="Biotech", sector="Health Care")],
        "stock": [_row(1.0, 0.3, symbol="AMGN", sector="Health Care", industry="Biotech")],
    }
    tree = B.build_tree(levels)
    assert [s["label"] for s in tree] == ["Health Care"]
    assert [i["label"] for i in tree[0]["industries"]] == ["Biotech"]
    assert [k["symbol"] for k in tree[0]["industries"][0]["stocks"]] == ["AMGN"]


def test_build_tree_keeps_a_stock_whose_industry_has_no_etf_row():
    """Four of the workbook's industries name an ETF another industry already
    owns, so the cascade reports them as ORPHANS with no industry row of their
    own. Their constituents still roll up to the sector and must not vanish."""
    levels = {
        "sector": [_row(1.0, 0.1, symbol="XLV", label="Health Care")],
        "industry": [],
        "stock": [_row(1.0, 0.3, symbol="AMGN", sector="Health Care",
                       industry="Cannabis")],
    }
    tree = B.build_tree(levels)
    assert tree[0]["industries"] == []
    assert [k["symbol"] for k in tree[0]["orphan_stocks"]] == ["AMGN"]


def test_build_tree_drops_a_row_whose_parent_sector_is_unknown():
    """A stock naming a sector with no sector row cannot be placed. Silently
    inventing a bucket for it would put a phantom row in the counts."""
    levels = {"sector": [], "industry": [],
              "stock": [_row(1.0, 0.3, symbol="ZZZ", sector="Nowhere",
                             industry="Nothing")]}
    assert B.build_tree(levels) == []


def test_build_tree_orders_sectors_strongest_first():
    levels = {"sector": [_row(-1.0, -0.1, symbol="XLU", label="Utilities"),
                         _row(2.0, 0.5, symbol="XLV", label="Health Care"),
                         _row(0.5, 0.1, symbol="XLF", label="Financials")],
              "industry": [], "stock": []}
    assert [s["label"] for s in B.build_tree(levels)] == \
        ["Health Care", "Financials", "Utilities"]


def test_build_tree_puts_unscored_sectors_last():
    levels = {"sector": [_row(None, None, symbol="XLU", label="Utilities"),
                         _row(-1.0, -0.1, symbol="XLE", label="Energy")],
              "industry": [], "stock": []}
    assert [s["label"] for s in B.build_tree(levels)] == ["Energy", "Utilities"]


def test_build_tree_sorts_a_non_finite_trend_as_unscored_rather_than_raising():
    """A bare float() here would let a NaN sort unpredictably — every comparison
    against it is False — and would let a signalling Decimal raise inside
    sorted(). Both are what _num exists to prevent."""
    from decimal import Decimal
    levels = {"sector": [_row(float("nan"), 0.1, symbol="A", label="A"),
                         _row(Decimal("sNaN"), 0.1, symbol="B", label="B"),
                         _row(1.0, 0.1, symbol="C", label="C")],
              "industry": [], "stock": []}
    assert [s["label"] for s in B.build_tree(levels)] == ["C", "A", "B"]


def test_build_tree_handles_an_empty_payload():
    assert B.build_tree({}) == []
    assert B.build_tree(None) == []
```

**Step 2: Run to verify it fails**

Expected: FAIL — no attribute `build_tree`

**Step 3: Implement**

```python
def _sort_key(row):
    """Strongest first; unscored rows last, then stable by their given order.

    Sorting on ``raw.trend`` rather than the cascade's blended ``score`` keeps
    the ordering on the same axis the map colours by — a row cannot appear above
    a greener one.

    Goes through ``_num``: a bare ``float(trend)`` would let a NaN sort
    unpredictably (every comparison against it is False) and would let a
    ``Decimal("sNaN")`` raise *inside* ``sorted()``, which is the exact class of
    bug ``_num`` exists to prevent.
    """
    trend = _num(((row or {}).get("raw") or {}).get("trend"))
    return (1, 0.0) if trend is None else (0, -trend)


def build_tree(levels):
    """levels{sector,industry,stock} -> nested sectors, strongest first.

    Each sector gains ``industries`` (each with its own ``stocks``) plus
    ``orphan_stocks`` — constituents whose industry has no row of its own. The
    source is the ADMISSION GATE: ``compute.py:2126`` builds ``industry_entries``
    only ``for i in universe["industries"] if i["etf"] in admitted``, while
    ``stock_entries`` at ``:2114`` admits every member regardless — so an
    industry ETF under the dollar-volume floor strands its whole membership at
    the sector level, and dropping them would silently shrink the tree.

    A row naming a parent that does not exist is DROPPED rather than placed in an
    invented bucket, which would put a phantom row into the counts.
    """
    levels = levels or {}
    sectors = sorted(levels.get("sector") or [], key=_sort_key)
    by_name = {}
    out = []
    for row in sectors:
        node = dict(row)
        node["industries"] = []
        node["orphan_stocks"] = []
        by_name[row.get("label")] = node
        out.append(node)

    industries = {}
    for row in sorted(levels.get("industry") or [], key=_sort_key):
        parent = by_name.get(row.get("sector"))
        if parent is None:
            continue
        node = dict(row)
        node["stocks"] = []
        parent["industries"].append(node)
        industries[(row.get("sector"), row.get("label"))] = node

    for row in sorted(levels.get("stock") or [], key=_sort_key):
        parent = by_name.get(row.get("sector"))
        if parent is None:
            continue
        industry = industries.get((row.get("sector"), row.get("industry")))
        (industry["stocks"] if industry else parent["orphan_stocks"]).append(dict(row))
    return out
```

**Step 4: Run to verify it passes**

Expected: PASS (21 passed)

**Step 5: Commit**

```bash
git add webgui/pages/bullbear.py webgui/tests/test_bullbear.py
git commit -m "feat(bullbear): nest the three levels into a sorted tree"
```

---

## Task 6: Tier-2 — merge the nightly tree with live quotes

**Files:**
- Modify: `services/sentiment_svc/compute.py`
- Test: `services/sentiment_svc/tests/test_bullbear.py` (create)

**Step 1: Write the failing test**

```python
"""Tier-2 merge for the Bull / Bear Map: nightly momentum + one live quote call."""
from services.sentiment_svc import compute


def test_bullbear_symbols_covers_all_three_levels_deduped():
    levels = {"sector": [{"symbol": "XLV"}], "industry": [{"symbol": "XBI"}],
              "stock": [{"symbol": "AMGN"}, {"symbol": "XBI"}]}
    syms = compute.bullbear_symbols(levels)
    assert syms == ["XLV", "XBI", "AMGN"]      # order preserved, deduped


def test_bullbear_symbols_skips_blank_symbols():
    levels = {"sector": [{"symbol": ""}, {"symbol": None}, {"symbol": "XLV"}]}
    assert compute.bullbear_symbols(levels) == ["XLV"]


def test_merge_live_attaches_the_day_move_to_every_level():
    levels = {"sector": [{"symbol": "XLV", "raw": {}}],
              "industry": [{"symbol": "XBI", "raw": {}}],
              "stock": [{"symbol": "AMGN", "raw": {}}]}
    quotes = {"XLV": {"quote": {"netPercentChange": 1.25}},
              "XBI": {"quote": {"netPercentChange": -0.5}},
              "AMGN": {"quote": {"netPercentChange": 0.0}}}
    merged = compute.merge_live(levels, quotes)
    assert merged["sector"][0]["day_pct"] == 1.25
    assert merged["industry"][0]["day_pct"] == -0.5
    assert merged["stock"][0]["day_pct"] == 0.0


def test_merge_live_leaves_day_pct_none_when_the_quote_is_missing():
    """Off-hours and for a halted name there is no quote. None renders as a dash;
    a 0.0 default would render as 'unchanged', which is a different claim."""
    levels = {"sector": [{"symbol": "XLV", "raw": {}}]}
    assert compute.merge_live(levels, {})["sector"][0]["day_pct"] is None
    assert compute.merge_live(levels, {"XLV": {}})["sector"][0]["day_pct"] is None


def test_merge_live_does_not_mutate_the_cached_momentum_payload():
    """The momentum view is a SHARED cached object. Mutating it here would leak
    a live field into /sentiment/momentum and into the next merge."""
    original = {"symbol": "XLV", "raw": {}}
    levels = {"sector": [original]}
    compute.merge_live(levels, {"XLV": {"quote": {"netPercentChange": 1.0}}})
    assert "day_pct" not in original
```

**Step 2: Run to verify it fails**

Run: `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc/tests/test_bullbear.py -q`
Expected: FAIL — no attribute `bullbear_symbols`

**Step 3: Implement (append to `services/sentiment_svc/compute.py`)**

```python
# ── Bull / Bear Map (cache:sentiment:bullbear) ──────────────────────────────
# The nightly cascade already scores all three levels with parent links. This
# adds only the live layer: ONE batched /quotes call for every distinct symbol
# (measured 374 on 2026-08-19, all returned in a single call), merged onto a COPY
# of the cached rows. See docs/plans/2026-08-19-bull-bear-map-design.md.

BULLBEAR_LEVELS = ("sector", "industry", "stock")

# sentiment_svc/compute.py has NO _PROJ_CT_TZ (that constant is options_svc's) —
# verified. CT matches how the cascade dates its own session.
_BULLBEAR_TZ = ZoneInfo("America/Chicago")   # add the import if absent


def bullbear_symbols(levels):
    """Every distinct symbol across the three levels, order preserved.

    Deduped because an industry ETF can also be a scored stock; the quote call
    should ask once.
    """
    out, seen = [], set()
    for name in BULLBEAR_LEVELS:
        for row in (levels or {}).get(name) or []:
            symbol = (row or {}).get("symbol")
            if symbol and symbol not in seen:
                seen.add(symbol)
                out.append(symbol)
    return out


def merge_live(levels, quotes):
    """Attach ``day_pct`` to a COPY of every row.

    Copies rather than mutates: ``levels`` comes from the cached momentum
    payload, which /sentiment/momentum also reads. Writing a live field into it
    would leak into that page and into the next merge.

    A missing quote leaves ``day_pct`` None, which renders as a dash. Defaulting
    to 0.0 would render as "unchanged" — a different and false claim.
    """
    merged = {}
    for name in BULLBEAR_LEVELS:
        rows = []
        for row in (levels or {}).get(name) or []:
            copy = dict(row or {})
            quote = ((quotes or {}).get(copy.get("symbol")) or {}).get("quote") or {}
            pct = quote.get("netPercentChange")
            copy["day_pct"] = float(pct) if isinstance(pct, (int, float)) else None
            rows.append(copy)
        merged[name] = rows
    return merged
```

**Step 4: Run to verify it passes**

Expected: PASS (5 passed)

**Step 5: Commit**

```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/tests/test_bullbear.py
git commit -m "feat(bullbear): tier-2 symbol list and live day-move merge"
```

---

## Task 7: Tier-2 — the view builder

**Files:**
- Modify: `services/sentiment_svc/compute.py`
- Test: `services/sentiment_svc/tests/test_bullbear.py`

**Step 1: Write the failing test**

```python
def test_bullbear_view_carries_levels_and_both_timestamps(monkeypatch):
    """Two clocks, deliberately: the scores are last night's, the day-moves are
    now. One timestamp would misdate one of them."""
    momentum = {"session_date": "2026-08-19",
                "computed_at": "2026-08-19T16:20:18-05:00",
                "levels": {"sector": [{"symbol": "XLV", "raw": {}}],
                           "industry": [], "stock": []}}
    monkeypatch.setattr(compute, "_bullbear_quotes",
                        lambda syms: {"XLV": {"quote": {"netPercentChange": 1.0}}})
    view = compute.bullbear_view(momentum)
    assert view["session_date"] == "2026-08-19"
    assert view["computed_at"] == "2026-08-19T16:20:18-05:00"
    assert view["quoted_at"]                       # its own, live clock
    assert view["levels"]["sector"][0]["day_pct"] == 1.0


def test_bullbear_view_degrades_to_the_nightly_tree_when_quotes_fail(monkeypatch):
    """A dead proxy must cost the day-move column, not the whole map."""
    def _boom(syms):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(compute, "_bullbear_quotes", _boom)
    view = compute.bullbear_view({"levels": {"sector": [{"symbol": "XLV", "raw": {}}]}})
    assert view["levels"]["sector"][0]["day_pct"] is None
    assert view["quoted_at"] is None


def test_bullbear_view_of_a_cold_momentum_cache_is_empty_not_broken():
    view = compute.bullbear_view(None)
    assert view["levels"] == {"sector": [], "industry": [], "stock": []}
    assert view["session_date"] is None
```

**Step 2: Run to verify it fails**

Expected: FAIL — no attribute `bullbear_view`

**Step 3: Implement**

```python
def _bullbear_quotes(symbols):
    """One batched quote call for every symbol. Measured: 374 symbols return in
    a SINGLE call, so this is one request per poll, not one per name.

    Uses ``_proxy.schwab_client.get_quotes(list)`` — the same accessor this
    module already uses in ``load_sector_perf`` (line ~245). NOTE
    ``services/_proxy.py`` itself exposes only ``health()``; the client is what
    carries the data calls.
    """
    from services import _proxy

    if not symbols:
        return {}
    return _proxy.schwab_client.get_quotes(list(symbols)) or {}


def bullbear_view(momentum) -> dict:
    """Merge the nightly cascade with live quotes -> cache:sentiment:bullbear.

    TWO timestamps on purpose: ``computed_at``/``session_date`` date the SCORES
    (last night's cascade) and ``quoted_at`` dates the day-moves (now). A single
    timestamp would misdate one of them, and the page states both.

    Fully defensive: a quote failure costs the day-move column and nothing else,
    and a cold momentum cache yields an empty tree rather than an exception.
    """
    momentum = momentum or {}
    levels = momentum.get("levels") or {}
    levels = {name: list(levels.get(name) or []) for name in BULLBEAR_LEVELS}
    quoted_at = None
    try:
        quotes = _bullbear_quotes(bullbear_symbols(levels))
        quoted_at = _dt.datetime.now(_BULLBEAR_TZ).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001 — the tree is still worth showing.
        log.exception("bullbear live quotes degraded → nightly tree only")
        quotes = {}
    return {
        "session_date": momentum.get("session_date"),
        "computed_at": momentum.get("computed_at"),
        "quoted_at": quoted_at,
        "regime": momentum.get("regime"),
        "levels": merge_live(levels, quotes),
    }
```

> **Verified, so do not re-derive:** `services/sentiment_svc/compute.py` already defines `import datetime as _dt` (line 19) and `log = logging.getLogger(__name__)` (line 29) — reuse both. It does **not** define `_PROJ_CT_TZ`; that constant belongs to `options_svc`, hence the local `_BULLBEAR_TZ`. And `services/_proxy.py` exposes **only `health()`** — quote calls go through `_proxy.schwab_client.get_quotes(list)`, which this same module already uses at lines ~245 and ~265.

**Step 4: Run to verify it passes**

Expected: PASS (8 passed)

**Step 5: Commit**

```bash
git add services/sentiment_svc/compute.py services/sentiment_svc/tests/test_bullbear.py
git commit -m "feat(bullbear): the merged view builder with two clocks"
```

---

## Task 8: Tier-2 — publish and schedule

**Files:**
- Modify: `services/sentiment_svc/handlers.py`, `services/sentiment_svc/scheduler.py`
- Test: `services/sentiment_svc/tests/test_bullbear.py`

**Step 1: Write the failing test**

```python
from shared.bus import Bus
from shared.contracts import Command
from services.sentiment_svc import handlers


def test_publish_bullbear_caches_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    bus.cache_set("cache:sentiment:momentum",
                  {"session_date": "2026-08-19",
                   "levels": {"sector": [{"symbol": "XLV", "raw": {}}]}})
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes", lambda s: {})
    sub = bus.subscribe("events:sentiment:bullbear")
    handlers.publish_bullbear(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()
    env = bus.cache_get("cache:sentiment:bullbear")
    assert env is not None
    assert env.payload["session_date"] == "2026-08-19"
    assert msg is not None and msg.get("version") == env.version


def test_publish_bullbear_survives_a_cold_momentum_cache(monkeypatch):
    """The map's own poll starts before the first nightly cascade on a fresh
    install. It must publish an empty tree, not raise into the scheduler."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes", lambda s: {})
    handlers.publish_bullbear(bus)
    assert bus.cache_get("cache:sentiment:bullbear").payload["levels"]["sector"] == []


def test_refresh_bullbear_command_is_dispatched(monkeypatch):
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers, "publish_bullbear", lambda b: calls.append(b))
    handlers.handle_command(bus, Command(type="refresh_bullbear"))
    assert calls == [bus]
```

**Step 2: Run to verify it fails**

Expected: FAIL — no attribute `publish_bullbear`

**Step 3: Implement**

In `handlers.py`, beside the other cache-key constants:

```python
CACHE_BULLBEAR = "cache:sentiment:bullbear"
EVENT_BULLBEAR = "events:sentiment:bullbear"
```

and the publisher:

```python
def publish_bullbear(bus) -> None:
    """Publish ``cache:sentiment:bullbear`` — the nightly tree plus live moves.

    Reads the momentum view from the BUS rather than recomputing it: the cascade
    is a multi-minute nightly job and this poll runs every ~30 s.
    """
    env = bus.cache_get(CACHE_MOMENTUM)
    momentum = env.payload if env is not None else None
    payload = compute.bullbear_view(momentum)
    bus.cache_set(CACHE_BULLBEAR, payload,
                  event=EVENT_BULLBEAR, skip_unchanged=True)
```

> **Pass `event=` rather than publishing separately, and let `cache_set` do both.** Verified in `shared/bus/client.py`: with `skip_unchanged=True` and a byte-identical payload it skips the `INCR`, the `SET` **and the event publish**, and returns the **existing** version — never `None`. So a `if version is not None` guard would publish on every tick and defeat the point. Handing it `event=` also pipelines the SET and PUBLISH into one round trip. This matters off-hours, when every quote is static and an unchanged payload must not wake every open tab every 30 s. (`{key}:ts` is still refreshed on a skip, so the Status page's freshness check will not read the poller as dead.)

Extend `handle_command`:

```python
    elif command.type == "refresh_bullbear":
        publish_bullbear(bus)
```

In `scheduler.py`, add the interval and an isolated loop modelled exactly on `_order_flow_publish_loop`:

```python
BULLBEAR_PUBLISH_SEC = 30      # live day-move cadence; one batched /quotes call


async def _bullbear_publish_loop(bus, loop_):
    """Publish cache:sentiment:bullbear every ~30 s off the event loop.

    Its own loop, like the order-flow one, so a publish failure can never break
    the composite refresh cadence — and so the map's fast cadence is not gated
    behind the 120 s composite tick.
    """
    while True:
        await asyncio.sleep(BULLBEAR_PUBLISH_SEC)
        try:
            await loop_.run_in_executor(None, handlers.publish_bullbear, bus)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort publish.
            log.debug("bullbear publish tick failed", exc_info=True)
```

Start and cancel it alongside `of_task` in the same `try`/`finally`.

**Step 4: Run the whole service suite**

Run: `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/sentiment_svc -q -rf`
Expected: the new tests PASS; the ONLY failure is the documented baseline `test_daily_history_wins_over_session_latch`.

**Step 5: Commit**

```bash
git add services/sentiment_svc/handlers.py services/sentiment_svc/scheduler.py services/sentiment_svc/tests/test_bullbear.py
git commit -m "feat(bullbear): publish cache:sentiment:bullbear on its own 30s loop"
```

---

## Task 9: The page — shell, headline, sector rows

**Files:**
- Create: `webgui/pages/sentiment_bullbear.py`
- Modify: `webgui/main.py`, `webgui/tests/test_shell.py`, `webgui/tests/test_no_inline_style.py`

**Step 1: Write the failing test** (in `webgui/tests/test_shell.py`, add `/sentiment/bullbear` to the expected route set; in `test_no_inline_style.py` add the new page to the guarded list)

```python
def test_bullbear_is_a_trend_and_sentiment_tab():
    from webgui import main
    routes = [r for r, _label, _icon in main.SENTIMENT_CHILDREN]
    assert "/sentiment/bullbear" in routes
    # third: the "where" that follows the "what"
    assert routes.index("/sentiment/bullbear") == 2
```

**Step 2: Run to verify it fails**

Run: `cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_shell.py -q`
Expected: FAIL — route not in `SENTIMENT_CHILDREN`

**Step 3: Implement**

In `main.py`, insert into `SENTIMENT_CHILDREN` at index 2:

```python
    ("/sentiment/bullbear", "Bull / Bear Map", "account_tree"),
```

Add the page route beside the other sentiment pages:

```python
@ui.page("/sentiment/bullbear")
def sentiment_bullbear_page() -> None:
    with _layout("/sentiment/bullbear", "Bull / Bear Map"):
        from pages import sentiment_bullbear
        sentiment_bullbear.render()
```

Add a badge colour entry alongside the other `/sentiment/*` routes if that map requires every route.

Create `webgui/pages/sentiment_bullbear.py` with `render()` that:
- reads `bus_client.read_full("sentiment:bullbear")`, graceful-empty when cold,
- shows the **two clocks** ("scores as of <session_date>, quotes <quoted_at>"),
- renders `B.headline(B.quadrant_counts(levels["sector"]), "sectors")` plus the full count distribution,
  — ⚠ **`headline` returns `""` on an empty payload, by design.** Task 3 made it suppress
  rather than render "0 of 0 sectors rising and leading", which read as a confidently
  bearish tape when the truth was that nothing had been published. That means this page
  MUST carry a real cold-cache empty state; rendering the headline unconditionally yields
  a blank strip with no explanation. This is a cross-task dependency currently held only
  by a docstring, so pin it with a test here.
- states which level the headline counts, since `noun` renders **verbatim** and the caller
  owns pluralisation (`B.headline` will happily emit "1 of 1 sectors"),
- renders one row per sector: label, quadrant chip (`B.quadrant_class`), trend, vs-SPY, live day-move, breadth bar,
  — ⚠ the breadth bar is the documented **continuous-value exception** to the fixed-palette
  rule, not the rule itself: 0-100 is 101 possible `w-[N%]` classes, so it must use the
  runtime arbitrary-value form reset via `.classes(remove=prev, add=new)`, per CLAUDE.md's
  `flex-[{w}_1_0%]` precedent. And `breadth_width` returns an **`int`** deliberately —
  `w-[50.0%]` is a class the Tailwind JIT will not generate.
  — ⚠ `width is None` (render no track at all) must be handled **distinctly** from
  `width == 0` (render an empty track). That distinction is the whole reason the function
  returns `None`, and a truthiness check at the call site collapses it. Pin it with a test.
- **Add `sentiment_bullbear.py` to `webgui/tests/test_no_inline_style.py` by hand** — that
  guard is an explicit file list, not a glob, so a new page silently escapes it otherwise.
- version-polls every 2 s via `ui.timer` and repaints only on a version change,
- wraps every timer/handler in `pages.ui_guard.guard`.

**Step 4: Run the tests**

Run: `cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_shell.py tests/test_no_inline_style.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/pages/sentiment_bullbear.py webgui/main.py webgui/tests/test_shell.py webgui/tests/test_no_inline_style.py
git commit -m "feat(bullbear): the page shell, headline and sector rows"
```

---

## Task 10: Lazy industry and stock expansion

**Files:**
- Modify: `webgui/pages/sentiment_bullbear.py`
- Test: `webgui/tests/test_bullbear.py`

Default screen is **11 rows**. Industries build on sector expand, stocks on industry expand — 376 rows are never all in the DOM.

**Step 1: Write the failing test**

```python
def test_expansion_is_lazy_so_the_default_screen_is_eleven_rows():
    """Structural guard: children must be built inside an expand handler, not at
    page build. 376 rows in the DOM would make every 2 s repaint expensive."""
    import inspect
    from pages import sentiment_bullbear as P
    src = inspect.getsource(P.render)
    assert "on_value_change" in src or "_expand" in src, \
        "industries/stocks must build on expand, not at page build"
```

**Step 2–4:** implement the expansion handlers using `ui.expansion`, building children on first open and caching the built rows so a re-open does not rebuild. Verify the guard passes.

**Step 5: Commit**

```bash
git commit -am "feat(bullbear): lazy industry and stock expansion"
```

---

## Task 11: The Desk sector strip

**Files:**
- Modify: `webgui/pages/desk.py`, `webgui/tests/test_desk.py`

**Step 1: Write the failing test**

```python
def test_desk_reads_the_bullbear_view():
    from pages import desk
    assert "sentiment:bullbear" in desk.VIEWS
    assert "sentiment:bullbear" in desk._REGION_VIEWS["bullbear"]


def test_desk_bullbear_strip_shows_every_sector():
    from pages import desk
    chips = desk.bullbear_chips({"levels": {"sector": [
        {"symbol": "XLV", "label": "Health Care", "raw": {"trend": 1.0, "excess": 0.1},
         "participation": 0.75, "day_pct": 0.4},
        {"symbol": "XLU", "label": "Utilities", "raw": {"trend": -1.0, "excess": -0.1},
         "participation": 0.16, "day_pct": -0.2}]}})
    assert [c["label"] for c in chips] == ["Health Care", "Utilities"]
    assert chips[0]["quadrant"] == "rising_leading"
    assert chips[1]["thin"] is True          # 0.16 participation


def test_desk_bullbear_strip_is_empty_when_the_view_is_cold():
    from pages import desk
    assert desk.bullbear_chips(None) == []
    assert desk.bullbear_chips({}) == []
```

**Step 2–4:** add `"sentiment:bullbear"` to `desk.VIEWS` and a `"bullbear"` entry in `_REGION_VIEWS`; implement `bullbear_chips` by delegating to `pages.bullbear` (do **not** re-implement `quadrant`); render the strip and make each chip navigate to `/sentiment/bullbear`.

**Step 5: Commit**

```bash
git commit -am "feat(bullbear): sector strip on the Desk, click through to the map"
```

---

## Task 12: Documentation

**Files:**
- Modify: `docs/webgui-routes.md`, `CLAUDE.md`, `webgui/page_help.py`, `docs/CHANGELOG.md`, `docs/manuals/reference-guide/reference-guide.md`, `docs/manuals/user-guide/user-guide.md`

Per CLAUDE.md's maintenance rules:
- **`docs/webgui-routes.md`** — a `/sentiment/bullbear` section: the two axes, the four quadrants, why there is no regime headline, the two clocks, the one-call quote batch, lazy expansion.
- **`CLAUDE.md`** — the route-table row only (a one-line entry plus a `[Detail]` link). Add the new view to the cache-key list if one is enumerated. **Do not** paste the feature narrative here.
- **`webgui/page_help.py`** — the hover guide, in plain language: what "Rising · Leading" means, why "Falling · Leading" is not bullish, what the breadth bar says, and that scores are from last night while the day-move is live.
- **Manuals** — a Reference Guide section (when to open it, how to read it, where it is weak: nightly scores, relative-strength caveat) and a User Guide bullet. **Rebuild with `python docs/manuals/build_docs.py`.**
- **`docs/CHANGELOG.md`** — the dated entry.

**Commit**

```bash
git add -A && git commit -m "docs(bullbear): route detail, page help, manuals and changelog"
```

---

## Task 13: Verify in dev, then promote

1. Full suites: `webgui`, `services/sentiment_svc`. Compare the failing **set** against the documented baselines, not the count.
2. Fast-forward `Using_Highcharts`, start dev (`start_dev.bat nowindow`), and **confirm the page actually works** — schedulers are suppressed in dev, so drive the publish with a command:
   ```python
   Bus().enqueue_command("cmd:sentiment", {"type": "refresh_bullbear"})
   Bus().cache_get("cache:sentiment:bullbear")
   ```
   Then open `http://127.0.0.1:9500/sentiment/bullbear` and check the tree expands, the counts match the payload, and the two clocks read correctly.
3. Only then merge to `main`, push, and run `tools\promote.bat` in the prod checkout. **Never** `git pull` in prod.

---

## Notes for the implementer

- **Do not blend the two axes into one score.** The whole design rests on keeping them separate; a combined number recreates exactly the ambiguity this page exists to remove.
- **Do not add a risk-on/risk-off headline.** Two existing screens already contradict each other doing that.
- `raw.trend` and `raw.excess` can be `None` — the cascade returns None for series too short or too thin to score. Every consumer must handle it; `quadrant` returns `"unknown"` rather than guessing.
- Stock rows have `participation: None`. That is not zero breadth.
