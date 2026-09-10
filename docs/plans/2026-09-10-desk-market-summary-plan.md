# Desk Market Summary + Regime Popup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Put a popup on the Market Regime word, and add a MARKET SUMMARY frame at the
bottom of the Desk: one Claude-written sentence consolidating Sentiment, Trend, Bias,
Signal, Regime and Bull/Bear, refreshed when those readings change, over six live chips.

**Architecture:** The sentence is written by `market_svc`'s existing ticker-narrative
Claude call, re-enabled and made change-driven: each poll builds a packet of the six
readings (version-gated off three cache views), fingerprints it at display resolution,
and calls Claude only when the fingerprint moves (≥10 min apart, ≤30/day). The
published `cache:market:summary` gains `inputs` + `as_of`. The Desk reads it as a new
`summary` region on its existing batched poll; chips come from views the Desk already
reads, so they are live even while the sentence lags. The ticker toggle stops gating the
call. Design: [`2026-09-10-desk-market-summary-design.md`](2026-09-10-desk-market-summary-design.md).

**Tech Stack:** Python 3.11, NiceGUI (Tier 1 `webgui/`), FastAPI service `market_svc`
(Tier 2), Redis via `shared.bus` (fakeredis under pytest), Anthropic SDK (Sonnet 5),
pytest.

**Conventions every task follows**
- Test runner (this Windows worktree has no venv of its own):
  ```powershell
  $py = "D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe"
  ```
  webgui tests run **inside** `webgui/` (`Push-Location webgui; & $py -m pytest ...; Pop-Location`);
  service / shared tests run from the repo root, **one service at a time**.
- Tailwind-first: no `.style(`, no `:style=`; colours come from the existing `CON_*` tokens.
- A `try/except Exception` body of ≥15 lines must log or call `_degrade.degraded`
  (`services/tests/test_no_silent_degrades.py`). Keep new guarded bodies short.
- Commit messages via `-m` with **no embedded double quotes** (Windows PowerShell 5.1
  splits native-exe arguments on them). Stage files **by name**, never `git add -A`.
- Compare the failing **set**, not the count (pytest defaults to `-rf`).

---

## Part A — The Regime popup

### Task 1: The popup table in `regime_mix.py`

**Files:**
- Modify: `webgui/pages/regime_mix.py` (after `ZERO_NOTE`, ~line 64)
- Test: `webgui/tests/test_regime_mix.py`

**Step 1: Write the failing tests** (append to `test_regime_mix.py`)

```python
# ── the Regime word's hover ──────────────────────────────────────────────────
# Every word the service can print: the five displays, the direction
# adornments, and "Unclear". The cross-tier test in shared/tests reads the
# service's own tables; this list is the page-side statement of the same set.
_REGIME_WORDS = ("Balanced", "Trending", "Rallying", "Firming", "Retreating",
                 "Softening", "Breakout", "Breakdown", "Whipsaw", "Stressed",
                 "Unclear")


def test_every_regime_word_has_a_picture():
    for word in _REGIME_WORDS:
        assert rm.regime_picture(word).strip(), word


def test_a_non_word_has_no_picture():
    for word in ("", None, "wat", "—", "balanced"):   # keys are the DISPLAY words
        assert rm.regime_picture(word) == "", word


def test_the_directional_pictures_name_their_direction():
    assert "higher" in rm.regime_picture("Rallying")
    assert "climb" in rm.regime_picture("Firming")
    assert "lower" in rm.regime_picture("Retreating")
    assert "decline" in rm.regime_picture("Softening")
    assert "downside" in rm.regime_picture("Breakdown")
```

**Step 2: Run to verify they fail**

Run: `Push-Location webgui; & $py -m pytest tests/test_regime_mix.py -q; Pop-Location`
Expected: 3 FAIL — `AttributeError: module 'pages.regime_mix' has no attribute 'regime_picture'`.

**Step 3: Implement** (insert after `ZERO_NOTE = "DORMANT"`)

```python
# The hover on the Market Regime word, keyed by the DISPLAYED word — the service
# publishes it with its direction already applied (``regime["label"]``), so the
# key is exactly what the reader sees. Covers the five REGIME_DISPLAY words, every
# direction adornment, and "Unclear"; shared/tests/test_cross_tier_mirrors.py
# fails if the service can print a word this table lacks.
REGIME_PICTURE = {
    "Balanced": "Quiet, two-sided tape: price is sitting at its own average, "
                "trend strength is low, and dealers are dampening moves. Neutral "
                "premium selling — iron condors — fits best.",
    "Trending": "Price is moving with persistence, but the two direction reads "
                "disagree on which way, so no direction is named. Follow the "
                "move once it shows; avoid fading it.",
    "Rallying": "A steep, persistent move higher, confirmed by both the price "
                "slope and the Market Trend score. Follow it; don't sell calls "
                "into it.",
    "Firming": "A steady, gentle climb, confirmed by both the price slope and "
               "the Market Trend score. Follow the direction; avoid fading it.",
    "Retreating": "A steep, persistent move lower, confirmed by both the price "
                  "slope and the Market Trend score. Follow it; don't sell puts "
                  "into it.",
    "Softening": "A steady, gentle decline, confirmed by both the price slope "
                 "and the Market Trend score. Follow the direction; avoid "
                 "fading it.",
    "Breakout": "The range is expanding into new ground. Momentum trades fit; "
                "credit spreads against the move are dangerous.",
    "Breakdown": "The range is expanding to the downside. Momentum favors the "
                 "downside; put credit spreads here are dangerous.",
    "Whipsaw": "Plenty of movement, no progress: failed breaks and two-sided "
               "wicks. The hardest regime — reduce size or stand aside.",
    "Stressed": "Fear is driving the tape: elevated VIX, an inverted volatility "
                "curve, gaps that don't fill. Premium is rich but the risk is "
                "real — defined risk only.",
    "Unclear": "No regime has enough evidence to name. Wait for one to form.",
}


def regime_picture(word):
    """The hover sentence for a displayed regime word, or "" for anything else."""
    return REGIME_PICTURE.get(str(word or "").strip(), "")
```

**Step 4: Run to verify they pass** — same command. Expected: all pass.

**Step 5: Commit**

```powershell
git add webgui/pages/regime_mix.py webgui/tests/test_regime_mix.py
git commit -m "feat(regime): a hover sentence for every Market Regime word"
```

---

### Task 2: Pin the regime words across tiers

**Files:**
- Modify: `shared/tests/test_cross_tier_mirrors.py` (after `test_sentiment_svc_delegates_rather_than_copying`)

**Step 1: Write the failing-until-Task-1 test** (it passes once Task 1 is in; run it to
confirm it is not vacuous by temporarily deleting one `REGIME_PICTURE` entry and seeing it fail)

```python
# --- the Regime word's hover --------------------------------------------------
# The service renders the displayed regime word (REGIME_DISPLAY + the
# _DIRECTIONAL adornments, or "Unclear" from regime_label); the webgui holds a
# hover per word. A word the service can print with no hover would render bare.

REGIME_PICTURE_PAGE = "webgui/pages/regime_mix.py"


def _regime_words():
    words = set(_const(REGIME_SOURCE, "REGIME_DISPLAY").values())
    for table in _const(REGIME_SOURCE, "_DIRECTIONAL").values():
        words |= set(table.values())
    # regime_label's own literal for an unknown key.
    return words | {"Unclear"}


def test_every_regime_word_the_service_can_print_has_a_hover():
    words = _regime_words()
    assert len(words) == 11, sorted(words)
    pictures = _const(REGIME_PICTURE_PAGE, "REGIME_PICTURE")
    missing = sorted(w for w in words if not pictures.get(w))
    assert not missing, (
        f"{REGIME_PICTURE_PAGE}:REGIME_PICTURE has no hover for {missing} - the "
        "service can print these words.")
```

**Step 2: Run** — `& $py -m pytest shared/tests/test_cross_tier_mirrors.py -q`.
Expected: PASS (with Task 1 in). Delete `"Unclear"` from `REGIME_PICTURE`, re-run →
FAIL naming `['Unclear']`. Restore it.

**Step 3: Commit**

```powershell
git add shared/tests/test_cross_tier_mirrors.py
git commit -m "test(mirrors): every regime word the service prints has a hover"
```

---

### Task 3: Hang the Regime popup on the Desk tile and the `/sentiment` dial

**Files:**
- Modify: `webgui/pages/desk.py` — `regime_display` (~line 953), the regime tile build
  (~line 3042), `_paint_strip` (~line 3171)
- Modify: `webgui/pages/console_regime.py` — `render_dial_card` (~line 118)
- Test: `webgui/tests/test_desk.py`, `webgui/tests/test_sentiment.py`

**Step 1: Write the failing tests**

In `test_desk.py`, after `test_sentiment_pill_hover_is_its_bias_words`:

```python
def test_regime_display_carries_the_regime_words_hover():
    from pages import regime_mix as RM
    reg = d.regime_display({"label": "Rallying", "committed_label": "trending",
                            "confidence": 0.7, "direction": 1})
    assert reg["tip"] == RM.regime_picture("Rallying")
    # A cold regime reads "Unclear" — and "Unclear" has its own sentence.
    assert d.regime_display(None)["tip"] == RM.regime_picture("Unclear")


def test_regime_tone_follows_the_committed_direction_only():
    assert d.regime_tone({"unclear": True, "direction": 1}) == d.CON_TXT_MUTED
    assert d.regime_tone({"unclear": False, "direction": 0}) == d.CON_TXT
    assert d.regime_tone({"unclear": False, "direction": 1}) == d.CON_POS
    assert d.regime_tone({"unclear": False, "direction": -1}) == d.CON_NEG


def test_render_hangs_the_regime_words_hover_on_the_strip(monkeypatch):
    from pages import regime_mix as RM
    _seed_bus(monkeypatch, _full_payloads())          # regime label "Rallying"
    texts = [t for t in _rendered_texts() if t]
    assert RM.regime_picture("Rallying") in texts
```

In `test_sentiment.py`, after `test_a_dashed_tile_carries_no_hover`:

```python
def test_hovering_the_regime_dial_describes_the_regime():
    from nicegui import ui
    from pages import regime_mix as RM
    bus_client.reset()
    _seed_cache()
    bus_client.bus().cache_set("cache:sentiment:regime", {
        "label": "Whipsaw", "committed_label": "choppy", "confidence": 0.62})
    card = _render_card()
    tips = [e.text for e in card.descendants()
            if isinstance(e, ui.tooltip)
            and isinstance(e.parent_slot.parent, ui.html)
            and 'id="regime-dial-' in (e.parent_slot.parent.content or "")]
    assert tips == [RM.regime_picture("Whipsaw")]
```

**Step 2: Run to verify they fail**

Run: `Push-Location webgui; & $py -m pytest tests/test_desk.py tests/test_sentiment.py -q; Pop-Location`
Expected: the four new tests FAIL (`KeyError: 'tip'`, `AttributeError: regime_tone`,
picture text missing, empty tooltip list).

**Step 3: Implement**

`desk.py` imports (beside the other `pages.*` imports):

```python
from pages.regime_mix import regime_picture as _regime_picture
```

`regime_display` — add the key (docstring: mention `tip`):

```python
    word = _CR.regime_name(r)
    return {
        "word": word,
        "committed_label": r.get("committed_label") or "",
        "confidence": _finite(r.get("confidence")),
        "direction": r.get("direction", 0),
        "direction_strong": bool(r.get("direction_strong")),
        "unclear": bool(r.get("unclear")),
        # The word's hover — regime_mix's own sentence, keyed by the displayed word.
        "tip": _regime_picture(word),
    }


def regime_tone(reg):
    """The regime word's colour: follows the direction the service committed,
    and ONLY when it committed one — a fixed green would paint "Retreating" as
    bullish. Shared by the strip tile and the summary chip."""
    if reg.get("unclear"):
        return CON_TXT_MUTED
    direction = reg.get("direction")
    if direction:
        return CON_POS if direction > 0 else CON_NEG
    return CON_TXT
```

Regime tile build — after `regime_lbl = ui.label(_DASH)...` add:

```python
                # The hover the regime word currently carries — "" at build.
                regime_tip = {"text": ""}
```

`_paint_strip` — replace the tone block with:

```python
        reg = regime_display(_view("sentiment:regime"))
        regime_lbl.text = reg["word"]
        regime_lbl.classes(remove=_ALL_STATE_TEXT, add=regime_tone(reg))
        # Updated in place, so the hover is swapped only when its sentence
        # changes — clearing an unchanged one would close it under the cursor.
        if reg["tip"] != regime_tip["text"]:
            regime_lbl.clear()
            _CC.pill_tooltip(regime_lbl, reg["tip"])
            regime_tip["text"] = reg["tip"]
```

`console_regime.py` — import and hang it on the dial:

```python
from pages.console_cards import pill_tooltip
```

```python
        dial = ui.html(console_dial.dial_svg(
            None if r.get("unclear") else r.get("confidence"), name,
            uid="regime")).classes("w-full max-w-[244px] self-center")
        # The dial's centre IS the regime word, so hovering it explains the word.
        pill_tooltip(dial, RM.regime_picture(name))
```

(`console_cards` imports only `pages.console` and `pages.options.theme`, so this adds no cycle.)

**Step 4: Run to verify they pass** — same command. Expected: all pass. Then run
`tests/test_console_regime.py` and `tests/test_no_inline_style.py` too.

**Step 5: Commit**

```powershell
git add webgui/pages/desk.py webgui/pages/console_regime.py webgui/tests/test_desk.py webgui/tests/test_sentiment.py
git commit -m "feat(regime): hovering the Regime word on the Desk and the dial explains it"
```

---

## Part B — The summary generator (`market_svc`)

### Task 4: `MarketSummary` gains `inputs` and `as_of`

**Files:**
- Modify: `shared/contracts/market.py:24-34`
- Test: `shared/contracts/tests/test_market_summary.py`

**Step 1: Failing test** (append)

```python
def test_inputs_and_as_of_are_additive_with_empty_defaults():
    """An older payload (narrative only) must still validate - the fields are
    additive, and the Desk treats empty ones as 'no provenance'."""
    m = MarketSummary(narrative="Quiet tape.")
    assert m.inputs == {} and m.as_of == ""
    full = MarketSummary(narrative="x", inputs={"bias": "Cautious"},
                         as_of="2026-09-10T15:42:00+00:00")
    back = MarketSummary.from_json(full.to_json())
    assert back.inputs == {"bias": "Cautious"}
    assert back.as_of == "2026-09-10T15:42:00+00:00"
```

**Step 2: Run** — `& $py -m pytest shared/contracts -q`. Expected: FAIL (no attribute `inputs`).

**Step 3: Implement** — in `MarketSummary`:

```python
    narrative: str = ""
    # The six readings the sentence was written from (market_svc's summary
    # packet). The Desk compares them with the live readings to say when the
    # sentence has been overtaken. Empty on an older writer.
    inputs: dict = {}
    # When the sentence was written (UTC ISO). The Desk prints it as "as of".
    as_of: str = ""
```

Update the class docstring: it is now change-driven and also feeds the Desk; the
"no per-payload timestamp" sentence becomes "`as_of` is when the sentence was written".

**Step 4: Run** — expected PASS.

**Step 5: Commit**

```powershell
git add shared/contracts/market.py shared/contracts/tests/test_market_summary.py
git commit -m "feat(contracts): MarketSummary carries its inputs and when it was written"
```

---

### Task 5: The summary packet — the six readings

**Files:**
- Modify: `services/market_svc/compute.py` (the summary section, ~line 310)
- Test: `services/market_svc/tests/test_summary.py`

**Step 1: Failing tests** — replace `test_build_summary_packet_extracts_compact_facts`
and add the helpers below (keep the other tests for now; Task 8 rewrites them):

```python
import datetime as dt
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")
_OPEN = dt.datetime(2026, 9, 10, 10, 0, tzinfo=_CT)      # Thursday, session open
_PRE = dt.datetime(2026, 9, 10, 7, 0, tzinfo=_CT)        # before the bell


def _composite(**trend):
    return {"live": {"composite": {"total_score": "3.98", "bias": "Cautious"}},
            "derived": {"size": "0.85x", "bias": "Cautious", "signal": "Bearish",
                        "trend": {"state": "lack_of_bearishness",
                                  "smoothed_score": 38.6, **trend}}}


def _regime(**over):
    return {"label": "Whipsaw", "committed_label": "choppy",
            "confidence": 0.62, **over}


def _row(sym, trend, excess, day_pct=None, day_excess=None):
    return {"symbol": sym, "raw": {"trend": trend, "excess": excess},
            "day_pct": day_pct, "day_excess": day_excess}


def _bullbear(benchmark=0.4):
    return {"benchmark_day_pct": benchmark, "levels": {"sector": [
        _row("XLK", 0.3, 0.2, day_pct=-0.5, day_excess=-0.9),   # quarter RL, today FL
        _row("XLU", -0.2, 0.1, day_pct=0.8, day_excess=0.4),    # quarter FL, today RL
        _row("XLE", -0.1, -0.3, day_pct=1.1, day_excess=0.7),   # quarter FLag, today RL
    ]}}


def test_packet_carries_the_six_readings_in_the_screens_words():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert p["sentiment"] == {"composite": 3.98}
    assert p["trend"] == {"word": "Gliding", "score": 38.6}
    assert (p["bias"], p["signal"], p["size"]) == ("Cautious", "Bearish", "0.85x")
    assert p["regime"] == {"word": "Whipsaw", "confidence": 0.62}


def test_packet_counts_bullbear_on_today_once_the_bell_has_rung():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert p["bullbear"]["horizon"] == "today"
    assert p["bullbear"]["counts"]["rising_leading"] == 2
    assert p["bullbear"]["counts"]["falling_lagging"] == 1


def test_packet_counts_bullbear_on_the_quarter_before_the_bell():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_PRE)
    assert p["bullbear"]["horizon"] == "quarter"
    assert p["bullbear"]["counts"]["rising_leading"] == 1


def test_packet_counts_the_quarter_when_no_benchmark_move_exists():
    """A dead proxy mid-session leaves benchmark_day_pct None - the Desk strip's
    rule: no benchmark, no 'today'."""
    p = compute.build_summary_packet(_composite(), _regime(),
                                     _bullbear(benchmark=None), now=_OPEN)
    assert p["bullbear"]["horizon"] == "quarter"


def test_packet_withholds_confidence_on_an_unclear_sample():
    p = compute.build_summary_packet(_composite(), _regime(unclear=True), {},
                                     now=_OPEN)
    assert p["regime"]["confidence"] is None
    assert p["regime"]["word"] == "Whipsaw"


def test_packet_quotes_no_prices():
    """Index moves, vol quotes and sector movers were dropped on purpose: a
    sentence quoting them is stale between refreshes."""
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert set(p) == {"sentiment", "trend", "bias", "signal", "size",
                      "regime", "bullbear"}


def test_packet_from_cold_caches_is_all_absent_never_neutral():
    p = compute.build_summary_packet({}, {}, {}, now=_OPEN)
    assert p["sentiment"]["composite"] is None and p["trend"]["word"] is None
    assert p["bias"] is None and p["signal"] is None
    assert p["regime"]["word"] == "Unclear"
    assert p["bullbear"] == {"horizon": None, "counts": {}}
```

**Step 2: Run** — `& $py -m pytest services/market_svc/tests/test_summary.py -q`.
Expected: new tests FAIL (`build_summary_packet() got an unexpected keyword 'now'` / wrong keys).

**Step 3: Implement** — in `compute.py`, add imports at the top:

```python
import math
from datetime import datetime

from shared import market_calendar as mc
```

Replace `build_summary_packet` with:

```python
# The five Market Trend flight words. MIRRORS the five-state entries of
# webgui/pages/sentiment._TREND_SHORT (this tier cannot import Tier 1); pinned by
# shared/tests/test_cross_tier_mirrors.py. The sentence must use the words the
# screen shows, or it names the trend one way while the pill beside it says another.
_TREND_WORDS = {"bullish": "Climbing", "lack_of_bullishness": "Stalling",
                "neutral": "Circling", "lack_of_bearishness": "Gliding",
                "bearish": "Diving"}

_QUADRANTS = ("rising_leading", "rising_lagging", "falling_leading",
              "falling_lagging", "unknown")


def _finite(v):
    """A real number or None — a NaN or a non-number is no reading, never 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _quadrant(trend, excess):
    """MIRRORS webgui/pages/bullbear.quadrant: ties go to the cautious side (a
    flat trend is not rising, a zero excess is not leading), and a missing axis
    is ``unknown`` rather than a default bucket."""
    if trend is None or excess is None:
        return "unknown"
    if trend > 0:
        return "rising_leading" if excess > 0 else "rising_lagging"
    return "falling_leading" if excess > 0 else "falling_lagging"


def bullbear_counts(bullbear, now=None):
    """``(horizon, {quadrant: n})`` over the sector rows.

    Today's axes once the regular session has opened AND a benchmark move
    exists; the quarter's otherwise — the Desk strip's own rule
    (``desk.strip_is_live``), so the sentence counts on the horizon the chips
    are drawn on. ``(None, {})`` when there are no rows to count."""
    view = bullbear if isinstance(bullbear, dict) else {}
    levels = view.get("levels") if isinstance(view.get("levels"), dict) else {}
    rows = levels.get("sector") if isinstance(levels.get("sector"), list) else []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None, {}
    live = (_finite(view.get("benchmark_day_pct")) is not None
            and mc.regular_session_has_opened(now or datetime.now().astimezone()))
    counts = {q: 0 for q in _QUADRANTS}
    for row in rows:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        trend, excess = ((row.get("day_pct"), row.get("day_excess")) if live
                         else (raw.get("trend"), raw.get("excess")))
        counts[_quadrant(_finite(trend), _finite(excess))] += 1
    return ("today" if live else "quarter"), counts


def build_summary_packet(sentiment, regime, bullbear, now=None):
    """The six readings the Desk shows, in the screen's own words — the ONLY
    facts the summary is written from. Prices are deliberately absent: a
    sentence quoting them goes stale between refreshes."""
    s = sentiment if isinstance(sentiment, dict) else {}
    live = s.get("live") if isinstance(s.get("live"), dict) else {}
    comp = live.get("composite") if isinstance(live.get("composite"), dict) else {}
    der = s.get("derived") if isinstance(s.get("derived"), dict) else {}
    trend = der.get("trend") if isinstance(der.get("trend"), dict) else {}
    r = regime if isinstance(regime, dict) else {}
    horizon, counts = bullbear_counts(bullbear, now)
    return {
        "sentiment": {"composite": _finite(comp.get("total_score"))},
        "trend": {"word": _TREND_WORDS.get(trend.get("state")),
                  "score": _finite(trend.get("smoothed_score",
                                              trend.get("score")))},
        "bias": der.get("bias") or None,
        "signal": der.get("signal") or None,
        "size": der.get("size") or None,
        # The Desk prints console_regime.regime_name: the service label, else
        # "Unclear". A withheld confidence stays withheld, as the console does.
        "regime": {"word": r.get("label") or "Unclear",
                   "confidence": (None if r.get("unclear")
                                  else _finite(r.get("confidence")))},
        "bullbear": {"horizon": horizon, "counts": counts},
    }
```

Delete `_tiles_by_cat` if nothing else uses it (grep first).

**Step 4: Run** — expected: the new packet tests PASS. (`test_generate_summary_*`
still call the old signature; they are rewritten in Task 8 — leave them failing only if
they fail; do not commit a red suite: if they fail, do Task 8's test edits now.)

**Step 5: Commit**

```powershell
git add services/market_svc/compute.py services/market_svc/tests/test_summary.py
git commit -m "feat(market_svc): the summary packet is the six Desk readings, no prices"
```

---

### Task 6: The fingerprint — what counts as a change

**Files:**
- Modify: `services/market_svc/compute.py`
- Test: `services/market_svc/tests/test_summary.py`

**Step 1: Failing tests**

```python
def _packet(**over):
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    p.update(over)
    return p


def test_fingerprint_ignores_movement_below_display_resolution():
    a = _packet()
    b = _packet(sentiment={"composite": 4.10})               # 3.98 -> 4.10: both 4.0
    b["trend"] = {"word": "Gliding", "score": 39.9}          # 38.6 -> 39.9: both 40
    assert compute.summary_fingerprint(a) == compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_word_changes():
    a = _packet()
    for key, val in (("bias", "Neutral"), ("signal", "Neutral")):
        assert compute.summary_fingerprint(a) != compute.summary_fingerprint(
            _packet(**{key: val})), key
    b = _packet()
    b["regime"] = {"word": "Balanced", "confidence": 0.62}
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_number_crosses_its_step():
    a = _packet()
    b = _packet(sentiment={"composite": 4.40})               # 4.0 -> 4.5
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_sector_changes_quadrant():
    a = _packet()
    b = _packet()
    b["bullbear"] = {"horizon": "today",
                     "counts": {**a["bullbear"]["counts"], "rising_leading": 3,
                                "falling_lagging": 0}}
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_nothing_to_summarize_has_no_fingerprint():
    """Cold caches must not trigger a paid call to describe nothing."""
    assert compute.summary_fingerprint(
        compute.build_summary_packet({}, {}, {}, now=_OPEN)) is None
    assert compute.summary_fingerprint(None) is None
```

**Step 2: Run** — expected FAIL (`summary_fingerprint` missing).

**Step 3: Implement**

```python
# Display resolution: a move smaller than these never reaches the reader's eye,
# so it must not buy a new sentence.
FINGERPRINT_COMPOSITE_STEP = 0.5
FINGERPRINT_TREND_STEP = 5.0
FINGERPRINT_CONFIDENCE_STEP = 0.1


def _bucket(v, step):
    return None if v is None else round(round(v / step) * step, 6)


def summary_fingerprint(packet):
    """What the sentence is written from, at display resolution — or None when
    there is nothing to summarize (no composite and no trend word).

    Words compare exactly; numbers to their step; Bull/Bear counts exactly with
    their horizon. Two packets with one fingerprint would get the same sentence."""
    p = packet if isinstance(packet, dict) else {}
    sent = p.get("sentiment") or {}
    trend = p.get("trend") or {}
    reg = p.get("regime") or {}
    bb = p.get("bullbear") or {}
    if sent.get("composite") is None and trend.get("word") is None:
        return None
    return (
        _bucket(sent.get("composite"), FINGERPRINT_COMPOSITE_STEP),
        trend.get("word"),
        _bucket(trend.get("score"), FINGERPRINT_TREND_STEP),
        p.get("bias"), p.get("signal"),
        reg.get("word"),
        _bucket(reg.get("confidence"), FINGERPRINT_CONFIDENCE_STEP),
        bb.get("horizon"),
        tuple(sorted((bb.get("counts") or {}).items())),
    )
```

**Step 4: Run** — expected PASS.

**Step 5: Commit**

```powershell
git add services/market_svc/compute.py services/market_svc/tests/test_summary.py
git commit -m "feat(market_svc): fingerprint the summary inputs at display resolution"
```

---

### Task 7: The change-driven gate

**Files:**
- Modify: `services/market_svc/scheduler.py` (replace `SUMMARY_RTH_SEC`,
  `SUMMARY_OFFHOURS_SEC`, `summary_due`)
- Test: `services/market_svc/tests/test_scheduler.py`

**Step 1: Failing tests** — delete `test_summary_due_fires_when_interval_elapsed`,
`test_summary_never_due_when_disabled`, `test_summary_due_defaults_to_enabled`; add:

```python
_DAY = dt.date(2026, 9, 10)
_FP = ("fp-a",)


def test_the_first_poll_after_a_restart_writes_a_sentence():
    assert sch.summary_due(sch.SummaryGate(), _FP, now_mono=0.0, today=_DAY)


def test_nothing_to_summarize_never_calls():
    assert not sch.summary_due(sch.SummaryGate(), None, now_mono=0.0, today=_DAY)


def test_an_unchanged_reading_never_calls_however_long_it_waits():
    gate = sch.record_summary(sch.SummaryGate(), _FP, now_mono=0.0, today=_DAY)
    assert not sch.summary_due(gate, _FP, now_mono=10 * 3600.0, today=_DAY)


def test_a_change_waits_out_the_minimum_gap():
    gate = sch.record_summary(sch.SummaryGate(), _FP, now_mono=0.0, today=_DAY)
    soon = sch.SUMMARY_MIN_GAP_SEC - 1
    assert not sch.summary_due(gate, ("fp-b",), now_mono=soon, today=_DAY)
    assert sch.summary_due(gate, ("fp-b",), now_mono=sch.SUMMARY_MIN_GAP_SEC,
                           today=_DAY)


def test_the_daily_ceiling_holds_and_resets_at_the_date_change():
    gate = sch.SummaryGate()
    t = 0.0
    for i in range(sch.SUMMARY_DAILY_CAP):
        fp = (f"fp-{i}",)
        assert sch.summary_due(gate, fp, now_mono=t, today=_DAY), i
        gate = sch.record_summary(gate, fp, now_mono=t, today=_DAY)
        t += sch.SUMMARY_MIN_GAP_SEC
    assert not sch.summary_due(gate, ("fp-new",), now_mono=t, today=_DAY)
    tomorrow = _DAY + dt.timedelta(days=1)
    assert sch.summary_due(gate, ("fp-new",), now_mono=t, today=tomorrow)


def test_the_gate_constants_are_the_approved_design():
    assert sch.SUMMARY_MIN_GAP_SEC == 10 * 60
    assert sch.SUMMARY_DAILY_CAP == 30
```

**Step 2: Run** — `& $py -m pytest services/market_svc/tests/test_scheduler.py -q`.
Expected: new tests FAIL (`SummaryGate` missing).

**Step 3: Implement** — replace the old block (lines ~64-89) with:

```python
# The summary is written ON CHANGE, not on a clock (2026-09-10): the Desk's
# MARKET SUMMARY frame and the ticker both show it, and a clock refresh paid for
# ~25 calls a day, most of them overnight rewrites of a market that had not
# moved. Now a sentence is written only when the readings' fingerprint moves
# (compute.summary_fingerprint), never twice within the gap, and never past the
# daily ceiling — so a reading flapping at a band boundary cannot run up cost.
SUMMARY_MIN_GAP_SEC = 10 * 60
SUMMARY_DAILY_CAP = 30


@dataclass(frozen=True)
class SummaryGate:
    fingerprint: tuple | None = None   # what the last sentence was written from
    last_call: float | None = None     # monotonic seconds of the last call
    day: _dt.date | None = None        # CT date the counter belongs to
    calls_today: int = 0


def summary_due(gate, fingerprint, *, now_mono, today):
    """Write a new sentence this poll? (pure)

    No — when there is nothing to summarize, when the readings have not changed
    since the last sentence, within ``SUMMARY_MIN_GAP_SEC`` of the last call, or
    once ``SUMMARY_DAILY_CAP`` calls have been made on ``today``. The first poll
    after a restart (empty gate) with readings present is always due."""
    if fingerprint is None:
        return False
    calls = gate.calls_today if gate.day == today else 0
    if calls >= SUMMARY_DAILY_CAP:
        return False
    if gate.fingerprint == fingerprint:
        return False
    if gate.last_call is not None and now_mono - gate.last_call < SUMMARY_MIN_GAP_SEC:
        return False
    return True


def record_summary(gate, fingerprint, *, now_mono, today):
    """The gate after a call is launched (pure) — a NEW gate, never mutated."""
    calls = gate.calls_today if gate.day == today else 0
    return SummaryGate(fingerprint=fingerprint, last_call=now_mono, day=today,
                       calls_today=calls + 1)
```

Add `from dataclasses import dataclass` to the imports.

**Step 4: Run** — expected PASS (the old `test_loop_gates_summary_on_the_enabled_flag`
still fails until Task 10 — note it, fix it there).

**Step 5: Commit** (only if the suite's failing set is exactly that one test; otherwise
continue to Task 10 first and commit together)

```powershell
git add services/market_svc/scheduler.py services/market_svc/tests/test_scheduler.py
git commit -m "feat(market_svc): write the summary on change, 10 min apart, 30 a day at most"
```

---

### Task 8: The version-gated packet reader and the new call

**Files:**
- Modify: `services/market_svc/compute.py` (`generate_summary`, constants)
- Test: `services/market_svc/tests/test_summary.py`, `services/market_svc/tests/test_env_claude_guard.py`

**Step 1: Failing tests** — replace the two `test_generate_summary_*` tests with:

```python
class _Msg:
    def __init__(self, text, stop="end_turn"):
        self.content = [type("B", (), {"text": text, "type": "text"})()]
        self.stop_reason = stop


def _client(text, stop="end_turn", seen=None):
    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                if seen is not None:
                    seen.update(kw)
                return _Msg(text, stop)
    return _C()


def test_generate_summary_returns_the_sentence_its_inputs_and_when():
    p = _packet()
    out = compute.generate_summary(p, client=_client("Fear builds; lean defensive."))
    assert out["narrative"] == "Fear builds; lean defensive."
    assert out["inputs"] == p
    assert out["as_of"].endswith("+00:00")


def test_generate_summary_sends_only_the_packet():
    import json
    seen = {}
    p = _packet()
    compute.generate_summary(p, client=_client("x", seen=seen))
    assert json.loads(seen["messages"][0]["content"]) == p
    assert seen["model"] == compute._SUMMARY_MODEL


def test_the_prompt_asks_for_the_consolidation_and_a_posture():
    s = compute._SUMMARY_SYSTEM.lower()
    for phrase in ("contrarian", "one reading", "agree or conflict", "posture",
                   "verbatim", "no prices"):
        assert phrase in s, phrase


def test_max_tokens_keeps_headroom():
    """A cap, not a spend: billing is on generated tokens. A trim below 300 risks
    a sentence cut mid-word that still renders as if complete."""
    assert compute._SUMMARY_MAX_TOKENS >= 300


def test_a_cut_off_reply_is_logged(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="market_svc.compute"):
        compute.generate_summary(_packet(), client=_client("Fear bu", stop="max_tokens"))
    assert any("max_tokens" in r.getMessage() for r in caplog.records)


def test_no_client_is_an_empty_sentence_never_a_made_up_one():
    out = compute.generate_summary(_packet(), client=None)
    assert out["narrative"] == "" and out["inputs"] == _packet()


def test_the_packet_reader_rebuilds_only_when_a_view_moves(monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    compute.reset_packet_memo()
    bus.cache_set("cache:sentiment:composite", _composite())
    bus.cache_set("cache:sentiment:regime", _regime())
    bus.cache_set("cache:sentiment:bullbear", _bullbear())
    builds = []
    real = compute.build_summary_packet
    monkeypatch.setattr(compute, "build_summary_packet",
                        lambda *a, **k: builds.append(1) or real(*a, **k))
    first = compute.read_summary_packet(bus, now=_OPEN)
    again = compute.read_summary_packet(bus, now=_OPEN)
    assert first == again and len(builds) == 1          # no version moved
    bus.cache_set("cache:sentiment:regime", _regime(label="Balanced"))
    assert compute.read_summary_packet(bus, now=_OPEN)["regime"]["word"] == "Balanced"
    assert len(builds) == 2
```

Open `test_env_claude_guard.py`; change
`test_generate_summary_degrades_to_empty_narrative` to call
`compute.generate_summary(<any packet dict>)` (the new signature) and assert
`out["narrative"] == ""`.

**Step 2: Run** — expected FAIL (signature, `inputs`, `read_summary_packet` missing).

**Step 3: Implement** — replace the summary constants and `generate_summary`:

```python
_SUMMARY_MODEL = "claude-sonnet-5"
# A cap, not a spend (billing is on generated tokens). Headroom so a sentence is
# never cut mid-word and still rendered as if complete; pinned by a test.
_SUMMARY_MAX_TOKENS = 300
_SUMMARY_MAX_CHARS = 400
_SUMMARY_SYSTEM = (
    "You are a terse markets desk analyst writing the one-line market summary on "
    "a trading desk. You get six readings as JSON, in the exact words the screen "
    "shows: sentiment (a 0-10 composite that is CONTRARIAN - a high score means "
    "the crowd is fearful, which this model reads as opportunity); trend (a word "
    "and a 0-100 score); bias and signal (two bands of that same composite, with "
    "a position size); regime (the tape's character, with a confidence); and "
    "bull/bear (how many of the 11 S&P sectors are rising or falling and leading "
    "or lagging SPY, counted today or on the quarter). Write at most TWO plain "
    "sentences (<=350 characters). Treat sentiment, bias and signal as ONE "
    "reading. Say where the readings agree or conflict. Close with a trading "
    "posture. Use the given words verbatim. No prices, no percent moves, no "
    "preamble, no disclaimers, no bullet points, no markdown."
)
```

Note the test looks for `"one reading"`, `"agree or conflict"`, `"verbatim"`, `"no prices"`
— keep those phrases (lower-cased match).

```python
_SUMMARY_KEYS = ("cache:sentiment:composite", "cache:sentiment:regime",
                 "cache:sentiment:bullbear")
_PACKET_MEMO = {"key": None, "packet": None}


def reset_packet_memo():
    """Drop the version-gated packet memo (test helper)."""
    _PACKET_MEMO.update(key=None, packet=None)


def read_summary_packet(bus, now=None):
    """The summary packet off its three views, rebuilt only when a version moved
    or the session-open horizon flipped.

    The Bull/Bear payload is ~190 KB and this runs on every 3 s poll: ungated it
    would be deserialized ~7,800 times a trading day for a packet that changes a
    handful of times. ``None`` on a read failure (the gate then skips)."""
    now = now or datetime.now().astimezone()
    try:
        vers = bus.cache_versions(_SUMMARY_KEYS)
        key = (tuple(vers.get(k) for k in _SUMMARY_KEYS),
               mc.regular_session_has_opened(now))
        if key == _PACKET_MEMO["key"] and _PACKET_MEMO["packet"] is not None:
            return _PACKET_MEMO["packet"]
        envs = [bus.cache_get(k) for k in _SUMMARY_KEYS]
        packet = build_summary_packet(*[(e.payload if e else {}) for e in envs],
                                      now=now)
        _PACKET_MEMO.update(key=key, packet=packet)
        return packet
    except Exception:  # noqa: BLE001
        log.warning("summary packet read failed", exc_info=True)
        return None


def generate_summary(packet, client=None):
    """Call Claude for the 1-2 sentence consolidated read + posture.

    Returns ``{"narrative", "inputs", "as_of"}``; an empty narrative (never a
    fabricated one) on dev / no key / API error. ``inputs`` is the packet the
    sentence was written from, so the Desk can tell when it has been overtaken."""
    import json
    from datetime import timezone
    out = {"narrative": "", "inputs": packet or {},
           "as_of": datetime.now(timezone.utc).isoformat()}
    c = client if client is not None else _make_summary_client()
    if c is None:
        return out
    try:
        _count_anthropic_call()
        resp = c.messages.create(
            model=_SUMMARY_MODEL, max_tokens=_SUMMARY_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(packet)}])
        if getattr(resp, "stop_reason", None) == "max_tokens":
            log.warning("market summary hit max_tokens (%d) - the sentence may "
                        "be cut", _SUMMARY_MAX_TOKENS)
        text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or [])
        out["narrative"] = " ".join(text.split()).strip()[:_SUMMARY_MAX_CHARS]
    except Exception:  # noqa: BLE001 — never raise out of a summary attempt.
        log.warning("market summary generation failed", exc_info=True)
    return out
```

⚠ The test `test_no_client_is_an_empty_sentence_never_a_made_up_one` passes
`client=None`, which resolves the REAL client — the service conftest's autouse
`_no_live_claude` fixture forces `_make_summary_client` to `None`, so no network. Confirm
that fixture exists in `services/market_svc/tests/conftest.py` before relying on it.

**Step 4: Run** — `& $py -m pytest services/market_svc -q`. Expected: all summary +
guard tests PASS (the one scheduler source test still fails — Task 10).

**Step 5: Commit**

```powershell
git add services/market_svc/compute.py services/market_svc/tests/test_summary.py services/market_svc/tests/test_env_claude_guard.py
git commit -m "feat(market_svc): the summary call consolidates six readings and returns its inputs"
```

---

### Task 9: Retire the ticker toggle's grip on the call (service side)

**Files:**
- Modify: `services/market_svc/handlers.py` (delete `CACHE_SUMMARY_ENABLED`,
  `set_summary_enabled`, `summary_enabled`; make `handle_command` a no-op)
- Modify: `services/market_svc/app.py` docstring (line ~6)
- Test: `services/market_svc/tests/test_handlers.py`

**Step 1: Failing test** — delete the four `summary_enabled` tests and
`test_handle_command_toggles_summary`; replace `test_handle_command_ignores_unknown_type`:

```python
def test_retired_toggle_commands_are_ignored_not_errors():
    """enable_summary / disable_summary were retired 2026-09-10. A fresh consumer
    group replays the stream backlog, so an old command WILL arrive - it must be
    a no-op, never an exception and never a gate."""
    bus = Bus()
    for t in ("disable_summary", "enable_summary", "nonsense"):
        handlers.handle_command(bus, Command(type=t))
    assert bus.cache_get("cache:market:summary_enabled") is None


def test_the_toggle_gate_is_gone():
    assert not hasattr(handlers, "summary_enabled")
    assert not hasattr(handlers, "set_summary_enabled")
```

**Step 2: Run** — expected FAIL (the gate still exists / writes the key).

**Step 3: Implement** — in `handlers.py` delete the three objects and replace
`handle_command`:

```python
def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:market`` command. There are none today.

    The ticker toggle's ``enable_summary`` / ``disable_summary`` were retired on
    2026-09-10, when the summary began feeding the Desk as well as the marquee —
    the toggle now only hides the marquee. Consumer groups replay the stream's
    backlog, so one of those can still arrive from an older webgui: it is
    ignored, like any unknown type."""
    log.debug("ignoring cmd:market %s", getattr(command, "type", None))
```

Update `app.py`'s docstring line naming `(enable_summary/disable_summary)`.

**Step 4: Run** — `& $py -m pytest services/market_svc -q`. Expected: handler tests
PASS; `test_loop_gates_summary_on_the_enabled_flag` now errors too (it greps for
`summary_enabled`) — fixed in Task 10.

**Step 5:** do not commit yet — Task 10 lands the scheduler loop that stops calling
`handlers.summary_enabled`; commit both together.

---

### Task 10: Wire the loop

**Files:**
- Modify: `services/market_svc/scheduler.py` (`_run_summary`, `loop`)
- Test: `services/market_svc/tests/test_scheduler.py`

**Step 1: Failing tests** — replace `test_loop_gates_summary_on_the_enabled_flag` with:

```python
def test_the_loop_is_gated_on_change_not_on_the_ticker_toggle():
    import inspect
    src = inspect.getsource(sch.loop)
    assert "summary_enabled" not in src
    assert "compute.read_summary_packet(bus)" in src
    assert "compute.summary_fingerprint(" in src
    assert "summary_due(" in src and "record_summary(" in src
```

Keep `test_loop_runs_summary_as_background_task`; update its last assertion to
`assert "await loop_.run_in_executor(None, compute.generate_summary" not in src`
(unchanged text — just confirm it still holds).

**Step 2: Run** — expected FAIL.

**Step 3: Implement**

```python
async def _run_summary(loop_, bus, packet) -> None:
    """Write the sentence + publish it, OFF the poll loop. Never raises."""
    try:
        summary = await loop_.run_in_executor(None, compute.generate_summary, packet)
        await loop_.run_in_executor(None, handlers.publish_summary, bus, summary)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a summary failure can't affect the poll loop.
        _log.exception("market summary generation failed")


async def loop(bus) -> None:
    """Poll → publish → (a new summary when the readings changed) → sleep."""
    loop_ = asyncio.get_running_loop()
    gate = SummaryGate()
    summary_task = None
    while True:
        interval = poll_interval()
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
            if summary_task is None or summary_task.done():
                packet = await loop_.run_in_executor(
                    None, compute.read_summary_packet, bus)
                fp = compute.summary_fingerprint(packet)
                mono, today = time.monotonic(), _dt.datetime.now(_CT).date()
                if summary_due(gate, fp, now_mono=mono, today=today):
                    gate = record_summary(gate, fp, now_mono=mono, today=today)
                    # A BACKGROUND task: the call can take ~60 s (30 s timeout +
                    # a retry) and must not stall the 3 s poll.
                    summary_task = asyncio.create_task(
                        _run_summary(loop_, bus, packet))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die.
            _log.exception("market poll cycle failed")
        await asyncio.sleep(interval)
```

⚠ The source test greps for `compute.read_summary_packet(bus)` — with the
`run_in_executor` form above the literal text is `compute.read_summary_packet, bus`.
Make the test assert `"compute.read_summary_packet" in src` instead (edit Step 1
accordingly). Add `import time`. Remove `secs_since_summary` / `summary_started`.
Update the module docstring's cadence paragraph (it still says ~2 s / 5 s; the
constants say 3 s / 15 s / 60 s — correct it in place).

**Step 4: Run** — `& $py -m pytest services/market_svc -q`. Expected: ALL PASS.

**Step 5: Commit** (Tasks 9 + 10)

```powershell
git add services/market_svc/handlers.py services/market_svc/app.py services/market_svc/scheduler.py services/market_svc/tests/test_handlers.py services/market_svc/tests/test_scheduler.py
git commit -m "feat(market_svc): the summary loop runs on change and the ticker toggle no longer gates it"
```

---

### Task 11: Retire the toggle's write-through (webgui side)

**Files:**
- Modify: `webgui/pages/settings.py:23-39` (`apply_ticker_enabled`), `:202-207` (card copy)
- Modify: `webgui/main.py:146-162` (delete `sync_ticker_setting`), `:2536` (its registration)
- Modify: `webgui/live_main.py:42-49` (docstring warning)
- Test: `webgui/tests/test_settings.py:20-56`, `webgui/tests/test_shell.py:490-538`

**Step 1: Failing tests**

`test_settings.py` — replace the two ticker tests:

```python
# ── ticker toggle ────────────────────────────────────────────────────────────
# Since 2026-09-10 the toggle only hides the marquee: the Claude summary also
# feeds the Desk's MARKET SUMMARY frame, so switching the marquee off must NOT
# stop it. The toggle therefore sends market_svc nothing.


def test_apply_ticker_enabled_persists_and_commands_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(S.app_settings, "_PATH", tmp_path / "settings.json")
    S.app_settings.reset_cache()
    sent = _capture(monkeypatch)
    S.apply_ticker_enabled(False)
    assert S.app_settings.load()["ticker_enabled"] is False
    S.apply_ticker_enabled(True)
    assert S.app_settings.load()["ticker_enabled"] is True
    assert sent == []
```

`test_shell.py` — replace the three `sync_ticker_setting` tests with:

```python
def test_the_ticker_resync_is_gone():
    """It existed only to re-assert the toggle's grip on market_svc's Claude
    call, retired 2026-09-10 — a startup hook with nothing left to sync."""
    import inspect

    import main
    assert not hasattr(main, "sync_ticker_setting")
    assert "sync_ticker_setting" not in inspect.getsource(main)
```

**Step 2: Run** — `Push-Location webgui; & $py -m pytest tests/test_settings.py tests/test_shell.py -q; Pop-Location`.
Expected: FAIL (a command is sent; `sync_ticker_setting` exists).

**Step 3: Implement**

`settings.py`:

```python
def apply_ticker_enabled(value) -> None:
    """Persist the ticker toggle. It only shows or hides the marquee.

    Until 2026-09-10 it also stopped market_svc's Claude call. That call now
    writes the Desk's MARKET SUMMARY sentence too, so hiding the marquee must
    not stop it — and it no longer costs a call on a clock: it is written only
    when the market readings change."""
    app_settings.set("ticker_enabled", bool(value))
```

Card copy:

```python
        ui.label("Scrolling market-summary marquee at the bottom of every page "
                 "(live data items + the Claude market summary, which also "
                 "feeds the Desk's Market Summary frame). Turning it off hides "
                 "the marquee only.").classes("opacity-70 text-sm")
```

If `bus_client` is now unused in `settings.py`, keep it only if another function uses it
(the captured auto-close and lifecycle toggles do — leave the import).

`main.py`: delete `sync_ticker_setting()` (lines 146-162) and the line
`app.on_startup(sync_ticker_setting)`. Fix the comment above the `on_startup` block if
it names the ticker specifically.

`live_main.py` docstring: remove `sync_ticker_setting` from the NEVER-call list and the
sentence about re-enabling the paid verdict; the other two sync functions stay listed.

**Step 4: Run** — same command, plus `tests/test_live_main.py`. Expected: PASS.

**Step 5: Commit**

```powershell
git add webgui/pages/settings.py webgui/main.py webgui/live_main.py webgui/tests/test_settings.py webgui/tests/test_shell.py
git commit -m "refactor(webgui): the ticker toggle hides the marquee only"
```

---

### Task 12: Pin the flight words in all three tiers

**Files:**
- Modify: `shared/tests/test_cross_tier_mirrors.py` (the trend-pill-words block)

**Step 1: Test**

```python
TREND_WORDS_SUMMARY = "services/market_svc/compute.py"
_FIVE_STATES = ("bullish", "lack_of_bullishness", "neutral",
                "lack_of_bearishness", "bearish")


def test_the_summary_names_the_trend_with_the_pills_words():
    """market_svc writes the Desk summary with these words; the pill beside it
    uses the page's. A rename in one would have the sentence and the pill name
    one trend two ways."""
    page = _const(TREND_WORDS_SOURCE, "_TREND_SHORT")
    summary = _const(TREND_WORDS_SUMMARY, "_TREND_WORDS")
    assert summary == {k: page[k] for k in _FIVE_STATES}
```

**Step 2: Run** — PASS; then change `"Gliding"` in `compute._TREND_WORDS` to
`"Resilient"`, re-run → FAIL; restore.

**Step 3: Commit**

```powershell
git add shared/tests/test_cross_tier_mirrors.py
git commit -m "test(mirrors): the summary's trend words match the pill's"
```

---

## Part C — The Desk frame

### Task 13: `summary_facts` — everything the frame draws, as plain data

**Files:**
- Modify: `webgui/pages/desk.py` (new section after `regime_tone`)
- Test: `webgui/tests/test_desk.py`

**Step 1: Failing tests** (new block after the regime tests)

```python
# ── the MARKET SUMMARY frame ─────────────────────────────────────────────────
def _summary(narrative="Fear builds while the tape glides; lean defensive.",
             **inputs):
    base = {"trend": {"word": "Gliding", "score": 38.6}, "bias": "Cautious",
            "signal": "Bearish", "regime": {"word": "Rallying", "confidence": 0.7},
            "bullbear": {"horizon": "today", "counts": {"rising_leading": 1}}}
    base.update(inputs)
    return {"narrative": narrative, "inputs": base,
            "as_of": "2026-09-10T15:42:00+00:00"}


def _summary_views(**over):
    views = {
        "summary": _summary(),
        "composite": {"live": {"composite": {"total_score": 3.98,
                                             "bias": "Cautious"}},
                      "derived": {"size": "0.85x", "bias": "Cautious",
                                  "signal": "Bearish",
                                  "trend": {"state": "lack_of_bearishness"}}},
        "history": {"snaps": []},
        "regime": {"label": "Rallying", "committed_label": "trending",
                   "confidence": 0.7, "direction": 1},
        "bullbear": _live_bullbear_payload(),
    }
    views.update(over)
    return views


def _facts(monkeypatch, live=True, **over):
    _live_now(monkeypatch, live=live)
    v = _summary_views(**over)
    return d.summary_facts(v["summary"], v["composite"], v["history"],
                           v["regime"], v["bullbear"])


def test_summary_facts_carry_the_sentence_and_when_it_was_written(monkeypatch):
    f = _facts(monkeypatch)
    assert f["narrative"].startswith("Fear builds")
    assert f["as_of"] == "as of 10:42 CT"


def test_summary_chips_are_the_six_readings_in_order(monkeypatch):
    f = _facts(monkeypatch)
    assert [c["key"] for c in f["chips"]] == [
        "sentiment", "trend", "bias", "signal", "regime", "bullbear"]
    vals = {c["key"]: c["value"] for c in f["chips"]}
    assert vals["sentiment"] == "3.98" and vals["trend"] == "Gliding"
    assert vals["bias"] == "Cautious" and vals["signal"] == "Bearish"
    assert vals["regime"] == "Rallying"
    assert vals["bullbear"].endswith("today")


def test_every_summary_chip_carries_its_own_hover(monkeypatch):
    from pages import regime_mix as RM
    from pages import sentiment as S
    tips = {c["key"]: c["tip"] for c in _facts(monkeypatch)["chips"]}
    assert tips["sentiment"] == d.SENTIMENT_TIP
    assert tips["trend"] == S.trend_picture("lack_of_bearishness")
    assert tips["bias"] == S.band_word_picture("bias", "Cautious")
    assert tips["signal"] == S.band_word_picture("signal", "Bearish")
    assert tips["regime"] == RM.regime_picture("Rallying")
    assert "Rising · Leading" in tips["bullbear"]
    assert "today" in tips["bullbear"]


def test_the_summary_is_current_when_the_readings_match_its_inputs(monkeypatch):
    assert _facts(monkeypatch)["moved"] is False


def test_the_summary_says_when_the_readings_moved_past_it(monkeypatch):
    moved = _summary(bias="Neutral")               # written when bias was Neutral
    assert _facts(monkeypatch, summary=moved)["moved"] is True
    regime_moved = _summary(regime={"word": "Whipsaw", "confidence": 0.4})
    assert _facts(monkeypatch, summary=regime_moved)["moved"] is True
    horizon_moved = _summary(bullbear={"horizon": "quarter",
                                       "counts": {"rising_leading": 1}})
    assert _facts(monkeypatch, summary=horizon_moved)["moved"] is True


def test_no_sentence_reads_as_no_sentence_never_as_current(monkeypatch):
    f = _facts(monkeypatch, summary={"narrative": "", "inputs": {}})
    assert f["narrative"] == "" and f["as_of"] == "" and f["moved"] is False
    assert _facts(monkeypatch, summary=None)["narrative"] == ""


def test_cold_readings_dash_and_carry_no_hover(monkeypatch):
    f = _facts(monkeypatch, composite={}, regime=None, bullbear=None,
               summary=None)
    by = {c["key"]: c for c in f["chips"]}
    for key in ("sentiment", "trend", "bias", "signal", "bullbear"):
        assert by[key]["value"] == d._DASH, key
        assert by[key]["tip"] == "", key
        assert by[key]["cls"] == d.CON_TXT_MUTED, key
    assert by["regime"]["value"] == "Unclear"      # the console's own cold word
```

`_live_bullbear_payload` counts ONE rising-and-leading sector today (XLK), so the
`_summary()` default `"rising_leading": 1` matches.

**Step 2: Run** — `Push-Location webgui; & $py -m pytest tests/test_desk.py -q; Pop-Location`.
Expected: the new tests FAIL (`summary_facts` missing).

**Step 3: Implement** (after `regime_tone`)

```python
# ── the MARKET SUMMARY frame ─────────────────────────────────────────────────
# One Claude-written sentence consolidating the six readings (market_svc's
# change-driven summary, cache:market:summary) over six LIVE chips read off the
# views this page already polls — so the chips are current even while the
# sentence lags. Design: docs/plans/2026-09-10-desk-market-summary-design.md.
SUMMARY_EMPTY = "No summary yet — one is written when the readings next change."
SUMMARY_MOVED = "Readings have changed since this was written."
SENTIMENT_TIP = ("The sentiment composite, 0–10. Contrarian: a higher score "
                 "means more fear, which this model reads as opportunity.")
_SUMMARY_HORIZON = {True: "today", False: "quarter"}


def _word_or_none(v):
    v = "" if v is None else str(v).strip()
    return None if v in ("", _DASH) else v


def bullbear_distribution(bullbear_view, now=None):
    """The Bull/Bear chip's hover: every quadrant's count and the horizon."""
    live = strip_is_live(bullbear_view, now)
    counts = _bb.quadrant_counts(_bullbear_rows(bullbear_view, live=live),
                                 live=live)
    if not sum(counts.values()):
        return ""
    parts = [f"{_bb.quadrant_label(q)} {counts[q]}"
             for q in _bb.QUADRANTS if counts[q]]
    horizon = "on today's moves" if live else "on the quarter"
    return f"{' · '.join(parts)} — counted {horizon}."


def _as_of_text(iso):
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return "" if dt.tzinfo is None else f"as of {dt.astimezone(_CT):%H:%M} CT"


def summary_facts(summary_view, composite_view, history_view, regime_view,
                  bullbear_view, now=None):
    """Everything the MARKET SUMMARY frame draws, as plain data:
    ``{"narrative", "as_of", "moved", "chips": [six {key,label,value,cls,tip}]}``.

    Every chip reuses the strip's own derivation (the pill composite, the band
    facts, ``regime_display``, the map's headline), so the frame and the strip
    cannot name one reading two ways. ``moved`` compares the WORDS and the
    Bull/Bear count the sentence was written from (``inputs``) with the live
    ones — a fact, not a promise of a refresh."""
    now = now or datetime.now().astimezone()
    summ = summary_view if isinstance(summary_view, dict) else {}
    comp = composite_view if isinstance(composite_view, dict) else {}
    hist = history_view if isinstance(history_view, dict) else {}
    derived = comp.get("derived") if isinstance(comp.get("derived"), dict) else {}
    snaps = hist.get("snaps") if isinstance(hist.get("snaps"), list) else []

    total = _finite(_pill_composite(comp.get("live"), snaps).get("total_score"))
    trend_word = _word_or_none(trend_pill_text(derived).title())
    band = {f["key"]: f for f in signal_band_facts(derived)}
    reg = regime_display(regime_view)
    live = strip_is_live(bullbear_view, now)
    counts = _bb.quadrant_counts(_bullbear_rows(bullbear_view, live=live),
                                 live=live)
    bb_line = bullbear_headline(bullbear_view, now)

    def _chip(key, label, value, cls, tip):
        return {"key": key, "label": label, "value": value or _DASH,
                "cls": cls if value else CON_TXT_MUTED,
                "tip": tip if value else ""}

    chips = [
        _chip("sentiment", "SENTIMENT",
              None if total is None else f"{total:.2f}", CON_TXT, SENTIMENT_TIP),
        _chip("trend", "TREND", trend_word, CON_TXT, trend_pill_tooltip(derived)),
        _chip("bias", "BIAS", _word_or_none(band["bias"]["value"]),
              band["bias"]["cls"], band["bias"]["tip"]),
        _chip("signal", "SIGNAL", _word_or_none(band["signal"]["value"]),
              band["signal"]["cls"], band["signal"]["tip"]),
        {"key": "regime", "label": "REGIME", "value": reg["word"],
         "cls": regime_tone(reg), "tip": reg["tip"]},
        _chip("bullbear", "BULL / BEAR", bb_line or None, CON_TXT,
              bullbear_distribution(bullbear_view, now)),
    ]

    narrative = str(summ.get("narrative") or "").strip()
    inputs = summ.get("inputs") if isinstance(summ.get("inputs"), dict) else {}
    moved = False
    if narrative and inputs:
        in_bb = inputs.get("bullbear") or {}
        was = ((inputs.get("trend") or {}).get("word"), inputs.get("bias"),
               inputs.get("signal"), (inputs.get("regime") or {}).get("word"),
               in_bb.get("horizon"),
               (in_bb.get("counts") or {}).get("rising_leading"))
        counted = bool(sum(counts.values()))
        now_is = (trend_word, _word_or_none(band["bias"]["value"]),
                  _word_or_none(band["signal"]["value"]), reg["word"],
                  _SUMMARY_HORIZON[live] if counted else None,
                  counts["rising_leading"] if counted else None)
        moved = was != now_is
    return {"narrative": narrative,
            "as_of": _as_of_text(summ.get("as_of")) if narrative else "",
            "moved": moved, "chips": chips}
```

⚠ Check `trend_pill_text(derived).title()`: `trend_pill_text` returns the UPPERCASE
word ("GLIDING"); `.title()` gives "Gliding", which equals `_TREND_WORDS[state]` for all
five flight words (single words). If a word ever gains a space, use
`_TREND_WORDS.get(_day_trend_state(derived))` instead — do that now if it reads cleaner.

**Step 4: Run** — expected: the new tests PASS, the rest of `test_desk.py` still green.

**Step 5: Commit**

```powershell
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): summary_facts - the frame's sentence, six live chips and their hovers"
```

---

### Task 14: Mount the frame and wire its region

**Files:**
- Modify: `webgui/pages/desk.py` — `VIEWS` (~line 1468), `_REGION_VIEWS` (~1477),
  `render()` after the panel grid (~line 3154), a `_paint_summary` painter, `painters`
- Test: `webgui/tests/test_desk.py`

**Step 1: Failing tests**

```python
def test_the_desk_reads_the_market_summary_on_its_one_poll():
    assert "market:summary" in d.VIEWS
    assert d._REGION_VIEWS["summary"] == (
        "market:summary", "sentiment:composite", "sentiment:history",
        "sentiment:regime", "sentiment:bullbear")


def test_render_mounts_the_market_summary_frame(monkeypatch):
    from pages import sentiment as S
    payloads = _full_payloads()
    payloads["market:summary"] = _summary(
        bias="Cautious", signal="Bearish",
        regime={"word": "Rallying", "confidence": 0.71})
    _seed_bus(monkeypatch, payloads)
    texts = [t for t in _rendered_texts() if t]
    assert "MARKET SUMMARY" in texts
    assert _summary()["narrative"] in texts
    assert S.band_word_picture("signal", "Bearish") in texts


def test_render_says_no_summary_yet_when_none_is_published(monkeypatch):
    _seed_bus(monkeypatch, _full_payloads())
    texts = [t for t in _rendered_texts() if t]
    assert d.SUMMARY_EMPTY in texts
```

Both existing wiring tests (`test_every_region_only_depends_on_views_the_page_actually_polls`,
`test_every_polled_view_feeds_at_least_one_region`) must keep passing.

**Step 2: Run** — expected FAIL.

**Step 3: Implement**

`VIEWS` — append `"market:summary"` (keep the tuple's comment accurate: "ten" becomes
"eleven" wherever the comments count them; fix in place).

`_REGION_VIEWS` — add:

```python
    # The sentence (market_svc, on change) and the five views its live chips
    # read. Chips and sentence update IN PLACE, so a repaint here costs nothing
    # visible when only a day-move ticked.
    "summary": ("market:summary", "sentiment:composite", "sentiment:history",
                "sentiment:regime", "sentiment:bullbear"),
```

`render()` — after the panel grid block (still inside the page column):

```python
        # ── the market summary ───────────────────────────────────────────────
        # At the BOTTOM, full width: the four panels above are per-symbol, and
        # this is the page's conclusion — every reading on the strip, said once.
        with ui.column().classes(f"{_TILE} w-full gap-[8px]"):
            with ui.row().classes("items-baseline w-full gap-4"):
                ui.label("MARKET SUMMARY").classes(_STRIP_EYEBROW)
                sum_asof = ui.label("").classes(
                    f"text-[11px] leading-none {CON_TXT_DIM}")
            sum_text = ui.label(SUMMARY_EMPTY).classes(
                f"text-[15px] leading-[1.5] {CON_TXT_MUTED}")
            sum_chips = []
            with ui.row().classes("items-baseline w-full gap-x-6 gap-y-1 flex-wrap"):
                for _fact in summary_facts(None, None, None, None, None)["chips"]:
                    with ui.row().classes("items-baseline gap-2"):
                        ui.label(_fact["label"]).classes(_STRIP_EYEBROW)
                        sum_chips.append(ui.label(_fact["value"]).classes(
                            f"text-[14px] {_fact['cls']}"))
            sum_moved = ui.label(SUMMARY_MOVED).classes(
                f"text-[11px] {CON_TXT_DIM}")
            sum_moved.set_visibility(False)
        # The hover each chip currently carries — "" at build (cold chips).
        sum_tips = ["" for _ in sum_chips]
```

Painter (beside `_paint_strip`):

```python
    def _paint_summary():
        f = summary_facts(_view("market:summary"), _view("sentiment:composite"),
                          _view("sentiment:history"), _view("sentiment:regime"),
                          _view("sentiment:bullbear"))
        sum_text.text = f["narrative"] or SUMMARY_EMPTY
        sum_text.classes(remove=_ALL_STATE_TEXT,
                         add=CON_TXT if f["narrative"] else CON_TXT_MUTED)
        sum_asof.text = f["as_of"]
        sum_moved.set_visibility(f["moved"])
        for i, (lbl, chip) in enumerate(zip(sum_chips, f["chips"])):
            lbl.text = chip["value"]
            lbl.classes(remove=_ALL_STATE_TEXT, add=chip["cls"])
            # In place: swap a hover only when its sentence changes.
            if chip["tip"] != sum_tips[i]:
                lbl.clear()
                _CC.pill_tooltip(lbl, chip["tip"])
                sum_tips[i] = chip["tip"]
```

`painters` — add `"summary": _paint_summary`.

Confirm `CON_TXT_DIM` is imported in `desk.py` (it is used by `bb_caption`); `_ALL_STATE_TEXT`
already contains `CON_TXT` / `CON_TXT_MUTED` / `CON_POS` / `CON_NEG` / `CON_WARN`.

**Step 4: Run** — `Push-Location webgui; & $py -m pytest tests/test_desk.py tests/test_no_inline_style.py tests/test_live_main.py -q; Pop-Location`.
Expected: PASS.

**Step 5: Commit**

```powershell
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): a MARKET SUMMARY frame at the bottom of the Desk"
```

---

## Part D — Docs, verification, rollout

### Task 15: Manuals, help, route notes, CHANGELOG

**Files:**
- `webgui/page_help.py` — the `/desk` entry: add a bullet after **Positions**:
  *Market Summary* — one sentence from Claude pulling the six readings together (sentiment,
  trend, bias, signal, regime, Bull/Bear), ending with a posture; written when the readings
  change, so it carries an "as of" time; the six chips under it are live; a dim line says
  when the readings have moved past the sentence. Mention hovering any word explains it.
  Also add "hover the regime word" to the top-strip bullet.
- `docs/manuals/user-guide/user-guide.md` — the Desk section (frame + Regime hover) and
  line ~1513 (the ticker: it now only hides the marquee; the summary is written on change).
- `docs/manuals/reference-guide/reference-guide.md` — the Desk page (frame), the
  `/sentiment` regime block (dial hover, with the Regime table from the design doc), and
  the ticker paragraph at ~line 3052 (remove "turning it off stops the Claude calls").
- `docs/manuals/technical-reference/technical-reference.md:1539` — the `market_svc` row:
  replace the 40/60-min clause with: summary written on change — fingerprint of the six
  readings (composite to 0.5, trend score to 5, confidence to 10 %, words and Bull/Bear
  counts exact), ≥10 min apart (`SUMMARY_MIN_GAP_SEC`), ≤30/day (`SUMMARY_DAILY_CAP`).
- `docs/manuals/api-reference/api-reference.md` — line ~180 (`MarketSummary`: add
  `inputs`, `as_of`), lines ~379-380 (replace the `SUMMARY_*` rows with the two new
  constants), lines ~395-396 (remove the two commands; say `cmd:market` has none and
  ignores replays), line ~533 (remove `cache:market:summary_enabled`).
- `docs/webgui-routes.md` — the `/desk` entry: the frame, its region, the Regime hover.
- `docs/CHANGELOG.md` — a new **Last updated** entry at the top (demote the current one
  to **Prior —**): what shipped, why the ticker call (measured ~20 Claude calls/weekday,
  ticker off since 2026-08-29), the change-driven gate and its numbers, the toggle
  retirement, the Regime popup, and the three-tier flight-word pin.

Then rebuild the four manuals:

```powershell
& $py docs/manuals/build_docs.py user-guide reference-guide technical-reference api-reference
```

Run `Push-Location webgui; & $py -m pytest tests/test_page_help*.py tests/test_manuals*.py -q; Pop-Location`
(whichever exist) and `& $py -m pytest shared/tests -q`.

Commit:

```powershell
git add webgui/page_help.py docs/manuals docs/webgui-routes.md docs/CHANGELOG.md
git commit -m "docs: the Desk market summary, the Regime hover, and a ticker toggle that only hides"
```

(`docs/manuals` stages the rebuilt `.html` / `.docx` too — confirm with `git status`
that nothing else under it changed.)

---

### Task 16: Full verification

Run every affected suite and compare the failing SET against the baseline (expected: none):

```powershell
Push-Location webgui; & $py -m pytest -q -p no:randomly; Pop-Location
& $py -m pytest services/market_svc -q
& $py -m pytest shared/tests -q
& $py -m pytest shared/contracts -q
& $py -m pytest services/tests -q
```

Grep for stragglers — each must return nothing outside CHANGELOG / dated plans:

```powershell
git grep -n -e summary_enabled -e enable_summary -e disable_summary -e sync_ticker_setting -e SUMMARY_RTH_SEC -- . ":!docs/CHANGELOG.md" ":!docs/plans"
```

---

### Task 17: Rollout (only on the user's go)

1. `git fetch origin`; confirm `origin/main` is an ancestor of `HEAD`; `git push origin HEAD:main`.
2. Pre-flight on the box: `ssh vps2-ts` → CT time, clean tree, stack/stream/live-capture
   active. On a trading day the default window is 15:25–16:15 CT unless the user says now.
3. Promote + restore the stream and live capture (promote stops them and does not restart them):
   ```bash
   cd /home/administrator/dev && ./tools/promote.sh && systemctl --user start trading-prod-stream.service && systemctl --user start trading-prod-live-capture.timer
   ```
4. Verify (pipe scripts as `$s | ssh vps2-ts "tr -d '\r\357\273\277' | bash -s"` — PowerShell
   prepends a BOM and CRLFs):
   - units restarted after the commit, none failed;
   - within a poll or two, `cache:market:summary` has a non-empty `narrative`, `inputs`
     with the six readings, and a fresh `as_of` (source `.env` for the Redis password;
     never print the key);
   - `curl -s http://127.0.0.1:8501/desk` contains `MARKET SUMMARY` and the narrative;
   - `shared/data/anthropic_call_counts.db` today's count rose by the calls made.
5. Later the same day: re-read today's count and report it against the 8–15 estimate.
6. Update memory: `ticker-toggle-gates-claude-call.md` is now wrong — rewrite it (the
   toggle hides the marquee only; the summary is change-driven and feeds the Desk) and
   fix its `MEMORY.md` line; add the measured calls/day to `api-call-volume-audit.md`.
