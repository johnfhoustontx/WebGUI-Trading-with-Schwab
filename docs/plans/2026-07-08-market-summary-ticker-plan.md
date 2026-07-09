# Market Summary Ticker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A scrolling ticker at the bottom of every webgui page that combines live rule-based market data items (from the dashboard + sentiment + trend caches) with a periodic Claude-written verdict.

**Architecture:** Two content streams. (1) `market_svc` gains a scheduled Claude call that publishes a short narrative verdict to `cache:market:summary`. (2) The webgui `_layout` shell renders a fixed bottom marquee that reads `cache:market:dashboard` + `cache:sentiment:composite` (for live color-coded items, built by a PURE Tier-1 builder) + `cache:market:summary` (the narrative), version-gated. A Settings toggle controls it.

**Tech Stack:** Python 3.11, FastAPI (`services/_scaffold`), Redis (`shared.bus`), pydantic contracts, `anthropic` SDK (lazy), NiceGUI (webgui), Tailwind, pytest.

**Design:** `docs/plans/2026-07-08-market-summary-ticker-design.md`.

**Reference patterns (read these — do not reinvent):**
- Claude call + key resolution: `services/options_svc/compute.py` — `_anthropic_api_key` (line ~1825), `_make_analyze_client` (~1843), `messages.create(model=_ANALYZE_MODEL, max_tokens=…, thinking={"type":"disabled"})` (~2193). Model `claude-sonnet-5`.
- Contract: `shared/contracts/market.py:MarketDashboard` + `shared/contracts/portfolio.py`.
- market_svc scheduler loop: `services/market_svc/scheduler.py` (`loop`, `poll_interval`, `_is_rth`, `_HOLIDAYS`).
- market_svc handlers publish: `services/market_svc/handlers.py` (`publish`, `cache_set(..., event=…, skip_unchanged=True)`).
- webgui shell: `webgui/main.py` `_layout` (`@contextmanager`, ~line 601; the `yield content` at the end; the `ui.timer(2.0, _tick)` pattern).
- Settings: `webgui/app_settings.py` (`DEFAULTS`, `get`, `set`), `webgui/pages/settings.py`.
- Tile color mapping (finite tone→class): `webgui/pages/market.py` `_BG`/`bg_class`.
- Version-gated reads: `webgui/bus_client.py` `read`, `read_full`, `read_version`, `read_versions`.

**Test commands:**
- Service: `.venv\Scripts\python -m pytest services\market_svc -q`
- Contracts: `.venv\Scripts\python -m pytest shared\contracts -q`
- Webgui: `cd webgui ; ..\.venv\Scripts\python -m pytest -q` (then `cd ..`)

---

## Task 1: `MarketSummary` contract

**Files:**
- Modify: `shared/contracts/market.py` (add the class)
- Create: `shared/contracts/tests/test_market_summary.py`

**Step 1: Write the failing test**

```python
# shared/contracts/tests/test_market_summary.py
from shared.contracts.market import MarketSummary


def test_defaults_and_round_trip():
    assert MarketSummary().narrative == ""
    assert MarketSummary().generated_at is None
    m = MarketSummary(narrative="Cautious tape.", generated_at="2026-07-08T12:00:00Z")
    assert MarketSummary.from_json(m.to_json()).narrative == "Cautious tape."
```

**Step 2: Run → fail**

Run: `.venv\Scripts\python -m pytest shared\contracts\tests\test_market_summary.py -q`
Expected: FAIL (ImportError: cannot import name 'MarketSummary')

**Step 3: Add to `shared/contracts/market.py`** (below `MarketDashboard`)

```python
class MarketSummary(_Base):
    """Market summary narrative payload (cache:market:summary).

    A short Claude-written verdict market_svc publishes on a schedule. The webgui
    ticker leads its scroll with this, followed by live rule-based data items.
    Defensive: an empty ``narrative`` (no key / API error) means the ticker shows
    live items only.
    """

    narrative: str = ""
    generated_at: str | None = None
```

**Step 4: Run → pass**

Run: `.venv\Scripts\python -m pytest shared\contracts -q`
Expected: PASS (all)

**Step 5: Commit**

```bash
git add shared/contracts/market.py shared/contracts/tests/test_market_summary.py
git commit -m "feat(market): MarketSummary contract"
```

---

## Task 2: market_svc summary compute (packet + Claude call)

**Files:**
- Modify: `services/market_svc/compute.py`
- Create: `services/market_svc/tests/test_summary.py`

`build_summary_packet` is PURE (compact facts from the two cache payloads) so it carries the
coverage. `_anthropic_api_key`/`_make_summary_client` mirror options_svc verbatim.
`generate_summary` is defensive (no client → empty narrative) and takes an injectable client
for testing.

**Step 1: Write the failing test** `services/market_svc/tests/test_summary.py`

```python
from services.market_svc import compute


def _dash():
    return {"categories": [
        {"category": "Volatility", "tiles": [
            {"display": "VIX", "last": 16.9, "change_pct": 4.8, "color_state": "risk_off_strong"},
            {"display": "SKEW", "last": 150.0, "change_pct": 2.8, "color_state": "risk_off_strong"}]},
        {"category": "Cash Index", "tiles": [
            {"display": "SPX", "last": 7482.0, "change_pct": -0.3, "color_state": "risk_off_mild"},
            {"display": "NDX", "last": 29252.0, "change_pct": 0.3, "color_state": "risk_on_mild"}]},
        {"category": "Sector SPDR", "tiles": [
            {"display": "XLK", "last": 181.0, "change_pct": 1.4, "color_state": "risk_on_strong"},
            {"display": "XLB", "last": 50.0, "change_pct": -2.6, "color_state": "risk_off_strong"}]},
    ]}


def _sent():
    return {"live": {"composite": {"total_score": "3.9", "bias": "Cautious"},
                     "sector_pcr": 1.34,
                     "breadth": {"interpretation": "A/D 0.41:1 - weak"}},
            "derived": {"trend": {"score": 42.7, "label": "Neutral"}}}


def test_build_summary_packet_extracts_compact_facts():
    p = compute.build_summary_packet(_dash(), _sent())
    assert p["sentiment"]["score"] == "3.9" and p["sentiment"]["bias"] == "Cautious"
    assert p["trend"]["label"] == "Neutral" and p["trend"]["score"] == 42.7
    assert p["put_call"] == 1.34
    # a few notable movers are captured
    assert any(m["display"] == "XLK" for m in p["movers"])
    assert "VIX" in {t["display"] for t in p["vol"]}


def test_generate_summary_no_client_is_empty_but_safe():
    out = compute.generate_summary(_dash(), _sent(), client=None)
    assert out["narrative"] == ""


def test_generate_summary_with_fake_client_returns_narrative():
    class _Msg:
        def __init__(self, text): self.content = [type("B", (), {"text": text, "type": "text"})()]
    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kw): return _Msg("Cautious, narrow tape — breadth weak.")
    out = compute.generate_summary(_dash(), _sent(), client=_FakeClient())
    assert "Cautious" in out["narrative"]
    assert len(out["narrative"]) <= compute._SUMMARY_MAX_CHARS + 50
```

**Step 2: Run → fail**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_summary.py -q`
Expected: FAIL

**Step 3: Add to `services/market_svc/compute.py`**

```python
_SUMMARY_MODEL = "claude-sonnet-5"
_SUMMARY_MAX_TOKENS = 220
_SUMMARY_MAX_CHARS = 400
_SUMMARY_SYSTEM = (
    "You are a terse markets desk analyst. Given a compact JSON snapshot of the "
    "current tape (sentiment, trend, breadth, volatility, index moves, sector/theme "
    "leaders), write ONE or TWO plain sentences (<=350 chars) summarizing the market "
    "read and posture. No preamble, no disclaimers, no bullet points, no markdown — "
    "just the sentences. Lead with the overall condition."
)


def _anthropic_api_key():
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        from repo_paths import SHARED_DIR
        p = SHARED_DIR / "anthropic_key.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        log.debug("reading anthropic_key.txt failed", exc_info=True)
    return None


def _make_summary_client():
    key = _anthropic_api_key()
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception:  # noqa: BLE001
        return None


def _tiles_by_cat(dashboard):
    out = {}
    for c in (dashboard or {}).get("categories", []):
        out[c.get("category")] = c.get("tiles", [])
    return out


def build_summary_packet(dashboard, sentiment):
    """PURE: compact facts for the summary prompt from the two cache payloads."""
    byc = _tiles_by_cat(dashboard)
    live = (sentiment or {}).get("live") or {}
    der = (sentiment or {}).get("derived") or {}
    comp = live.get("composite") or {}
    trend = der.get("trend") or {}

    def _movers(cats, n=3):
        tiles = [t for c in cats for t in byc.get(c, []) if t.get("change_pct") is not None]
        tiles.sort(key=lambda t: abs(t.get("change_pct") or 0), reverse=True)
        return [{"display": t["display"], "change_pct": t["change_pct"]} for t in tiles[:n]]

    return {
        "sentiment": {"score": comp.get("total_score"), "bias": comp.get("bias")},
        "trend": {"label": trend.get("label"), "score": trend.get("score")},
        "breadth": (live.get("breadth") or {}).get("interpretation"),
        "put_call": live.get("sector_pcr"),
        "vol": [{"display": t["display"], "last": t.get("last"), "change_pct": t.get("change_pct")}
                for t in byc.get("Volatility", [])],
        "index": [{"display": t["display"], "change_pct": t.get("change_pct")}
                  for t in byc.get("Cash Index", [])],
        "movers": _movers(["Sector SPDR", "Thematic / Industry ETF"], 4),
    }


def generate_summary(dashboard, sentiment, client=None):
    """Build the packet, call Claude for a 1-2 sentence verdict. Defensive → {'narrative': ''}."""
    import json
    packet = build_summary_packet(dashboard, sentiment)
    c = client if client is not None else _make_summary_client()
    if c is None:
        return {"narrative": ""}
    try:
        resp = c.messages.create(
            model=_SUMMARY_MODEL, max_tokens=_SUMMARY_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(packet)}])
        text = ""
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "text" or hasattr(block, "text"):
                text += getattr(block, "text", "")
        text = " ".join(text.split()).strip()[:_SUMMARY_MAX_CHARS]
        return {"narrative": text}
    except Exception:  # noqa: BLE001 — never raise out of a summary attempt.
        log.warning("market summary generation failed", exc_info=True)
        return {"narrative": ""}
```

Note: some Anthropic SDK versions may not accept `thinking={"type":"disabled"}` — the app
already uses it for Sonnet 5 in options_svc, so it's fine here. `SHARED_DIR` exists in
`repo_paths` (used by options_svc). If `SHARED_DIR` is not importable, use `from repo_paths
import SHARED` and `SHARED / "anthropic_key.txt"` — verify the actual name first.

**Step 4: Run → pass**

Run: `.venv\Scripts\python -m pytest services\market_svc\tests\test_summary.py -q`
Expected: PASS (3 passed)

**Step 5: Commit**

```bash
git add services/market_svc/compute.py services/market_svc/tests/test_summary.py
git commit -m "feat(market): summary packet + Claude narrative generation (defensive)"
```

---

## Task 3: market_svc publish + scheduler wiring

**Files:**
- Modify: `services/market_svc/handlers.py` (add `publish_summary`)
- Modify: `services/market_svc/scheduler.py` (add `summary_due` + loop branch)
- Modify: `services/market_svc/tests/test_handlers.py` and `test_scheduler.py`

**Step 1: Handler test** (append to `services/market_svc/tests/test_handlers.py`)

```python
def test_publish_summary():
    from shared.bus import Bus
    from services.market_svc import handlers
    bus = Bus()
    v = handlers.publish_summary(bus, {"narrative": "Cautious tape.", "generated_at": None})
    assert v >= 1
    env = bus.cache_get(handlers.CACHE_SUMMARY)
    assert env.payload["narrative"] == "Cautious tape."
```

**Step 2: Scheduler test** (append to `services/market_svc/tests/test_scheduler.py`)

```python
def test_summary_due_fires_when_interval_elapsed():
    from services.market_svc import scheduler as sch
    import datetime as dt
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    rth = dt.datetime(2026, 7, 7, 10, 0, tzinfo=ct)
    # never run → due
    assert sch.summary_due(None, secs_since=0, now=rth) is True
    # just ran → not due
    assert sch.summary_due(1.0, secs_since=10, now=rth) is False
    # RTH interval elapsed → due
    assert sch.summary_due(1.0, secs_since=sch.SUMMARY_RTH_SEC + 1, now=rth) is True
    # off-hours uses the longer interval
    off = dt.datetime(2026, 7, 7, 22, 0, tzinfo=ct)
    assert sch.summary_due(1.0, secs_since=sch.SUMMARY_RTH_SEC + 1, now=off) is False
    assert sch.summary_due(1.0, secs_since=sch.SUMMARY_OFFHOURS_SEC + 1, now=off) is True
```

**Step 3: Run → fail** both.

**Step 4: Implement.**

In `services/market_svc/handlers.py`:
```python
from shared.contracts.market import MarketDashboard, MarketSummary   # add MarketSummary
...
CACHE_SUMMARY = "cache:market:summary"
EVENT_SUMMARY = "events:market:summary"


def publish_summary(bus, payload) -> int:
    ms = MarketSummary(**payload)
    return bus.cache_set(CACHE_SUMMARY, ms.model_dump(), event=EVENT_SUMMARY, skip_unchanged=True)
```

In `services/market_svc/scheduler.py`:
```python
SUMMARY_RTH_SEC = 20 * 60       # refresh the Claude verdict every ~20 min during RTH
SUMMARY_OFFHOURS_SEC = 60 * 60  # ~hourly off-hours


def summary_due(last_run, secs_since, *, now=None):
    """Whether to regenerate the Claude verdict this cycle (pure).

    ``last_run`` is None until the first run (→ always due). Otherwise fire when
    ``secs_since`` the last run exceeds the RTH/off-hours interval.
    """
    if last_run is None:
        return True
    now = now or _dt.datetime.now(_CT)
    threshold = SUMMARY_RTH_SEC if _is_rth(now) else SUMMARY_OFFHOURS_SEC
    return secs_since >= threshold
```

Wire into `loop` (add a summary branch after the publish, tracking elapsed time). Update
`loop` to:
```python
async def loop(bus) -> None:
    loop_ = asyncio.get_running_loop()
    last_summary = None
    secs_since_summary = 0.0
    while True:
        interval = poll_interval()
        try:
            payload = await loop_.run_in_executor(None, compute.collect, bus)
            await loop_.run_in_executor(None, handlers.publish, bus, payload)
            if summary_due(last_summary, secs_since_summary):
                sent = bus.cache_get("cache:sentiment:composite")
                sent_payload = sent.payload if sent else {}
                summary = await loop_.run_in_executor(
                    None, compute.generate_summary, payload, sent_payload)
                await loop_.run_in_executor(None, handlers.publish_summary, bus, summary)
                last_summary = True
                secs_since_summary = 0.0
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.exception("market poll cycle failed")
        await asyncio.sleep(interval)
        secs_since_summary += interval
```
(Keep `poll_interval()` computed once per loop so the sleep and the accounting agree.)

**Step 5: Run → pass**

Run: `.venv\Scripts\python -m pytest services\market_svc -q`
Expected: PASS (all). The app test's TestClient lifespan runs the loop once; with no key
`generate_summary` returns empty → `publish_summary` caches an empty narrative. Harmless.

**Step 6: Commit**

```bash
git add services/market_svc/handlers.py services/market_svc/scheduler.py services/market_svc/tests/
git commit -m "feat(market): schedule + publish Claude market-summary verdict (cache:market:summary)"
```

---

## Task 4: webgui ticker — pure builders

**Files:**
- Create: `webgui/pages/ticker.py`
- Create: `webgui/tests/test_ticker.py`

**Step 1: Write the failing test** `webgui/tests/test_ticker.py`

```python
from pages import ticker


def _dash():
    return {"categories": [
        {"category": "Volatility", "tiles": [
            {"display": "VIX", "last": 16.9, "change_pct": 4.8, "color_state": "risk_off_strong"},
            {"display": "SKEW", "last": 150.0, "change_pct": 2.8, "color_state": "risk_off_strong"}]},
        {"category": "Cash Index", "tiles": [
            {"display": "SPX", "last": 7482.0, "change_pct": -0.3, "color_state": "risk_off_mild"},
            {"display": "NDX", "last": 29252.0, "change_pct": 0.3, "color_state": "risk_on_mild"}]},
        {"category": "Sector SPDR", "tiles": [
            {"display": "XLK", "last": 181.0, "change_pct": 1.4, "color_state": "risk_on_strong"},
            {"display": "XLB", "last": 50.0, "change_pct": -2.6, "color_state": "risk_off_strong"}]},
    ]}


def _sent():
    return {"live": {"composite": {"total_score": "3.9", "bias": "Cautious"},
                     "sector_pcr": 1.34,
                     "breadth": {"interpretation": "A/D 0.41:1 - weak"}},
            "derived": {"trend": {"score": 42.7, "label": "Neutral"}}}


def test_ticker_items_composes_expected_items():
    items = ticker.ticker_items(_dash(), _sent())
    texts = " | ".join(i["text"] for i in items)
    assert "Cautious" in texts and "3.9" in texts
    assert "Neutral" in texts and "42.7" in texts
    assert "VIX" in texts and "SKEW" in texts
    assert "SPX" in texts and "NDX" in texts
    assert "1.34" in texts  # put/call
    # every item carries a known tone
    assert all(i["tone"] in {"risk_on", "risk_off", "neutral", "warn"} for i in items)


def test_item_class_maps_every_tone_to_fixed_class():
    for tone in ("risk_on", "risk_off", "neutral", "warn"):
        assert isinstance(ticker.item_class(tone), str) and ticker.item_class(tone)
    assert ticker.item_class("bogus") == ticker.item_class("neutral")


def test_ticker_items_empty_caches_safe():
    assert ticker.ticker_items(None, None) == []
    assert ticker.ticker_items({}, {}) == []
```

**Step 2: Run → fail**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests\test_ticker.py -q ; cd ..`
Expected: FAIL (ModuleNotFoundError: pages.ticker)

**Step 3: Write `webgui/pages/ticker.py`** (pure part; the render part comes in Task 5)

```python
"""Market Summary Ticker (/ every page) — Tier-1, engine-free.

Reads cache:market:dashboard + cache:sentiment:composite (live items) and
cache:market:summary (Claude verdict), renders a fixed bottom marquee. Pure
builders here; render_ticker() (Task 5) does the widget + timer wiring.
"""

# tone → fixed Tailwind text class (finite map, Tailwind-first).
_TONE = {
    "risk_on": "text-emerald-400",
    "risk_off": "text-rose-400",
    "neutral": "text-slate-300",
    "warn": "text-amber-400",
}


def item_class(tone):
    return _TONE.get(tone, _TONE["neutral"])


def _tone_from_state(color_state):
    if not color_state:
        return "neutral"
    if color_state.startswith("risk_on"):
        return "risk_on"
    if color_state.startswith("risk_off"):
        return "risk_off"
    return "neutral"


def _fmt_pct(v):
    try:
        f = float(v)
        return f"{'+' if f >= 0 else ''}{f:.1f}%"
    except (TypeError, ValueError):
        return ""


def _fmt(v, nd=2):
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _tiles_by_cat(dashboard):
    out = {}
    for c in (dashboard or {}).get("categories", []):
        out[c.get("category")] = c.get("tiles", [])
    return out


def ticker_items(dashboard, sentiment):
    """List of {text, tone} live items from the two cache payloads (PURE)."""
    if not dashboard and not sentiment:
        return []
    byc = _tiles_by_cat(dashboard)
    live = (sentiment or {}).get("live") or {}
    der = (sentiment or {}).get("derived") or {}
    comp = live.get("composite") or {}
    trend = der.get("trend") or {}
    items = []

    # Sentiment + trend headline (tone by score band)
    if comp.get("bias"):
        score = comp.get("total_score")
        tone = "risk_off" if _as_float(score, 5) < 4.5 else ("risk_on" if _as_float(score, 5) > 6.5 else "neutral")
        items.append({"text": f"Sentiment {comp['bias']} {score}/10", "tone": tone})
    if trend.get("label"):
        ts = trend.get("score")
        tone = "risk_off" if _as_float(ts, 50) < 45 else ("risk_on" if _as_float(ts, 50) > 55 else "neutral")
        items.append({"text": f"Trend {trend['label']} {_fmt(ts, 1)}", "tone": tone})

    # Breadth
    br = (live.get("breadth") or {}).get("interpretation")
    if br:
        items.append({"text": f"Breadth {br}", "tone": "warn" if "weak" in br.lower() or "bearish" in br.lower() else "neutral"})

    # Volatility (VIX/VIX1D/SKEW) — inverted feel already baked in color_state
    for t in byc.get("Volatility", []):
        items.append({"text": f"{t['display']} {_fmt(t.get('last'))} {_fmt_pct(t.get('change_pct'))}".strip(),
                      "tone": _tone_from_state(t.get("color_state"))})

    # Put/Call
    pcr = live.get("sector_pcr")
    if pcr is not None:
        items.append({"text": f"P/C {_fmt(pcr)}", "tone": "risk_off" if _as_float(pcr, 1) > 1.0 else "risk_on"})

    # Indices
    for t in byc.get("Cash Index", []):
        items.append({"text": f"{t['display']} {_fmt_pct(t.get('change_pct'))}",
                      "tone": _tone_from_state(t.get("color_state"))})

    # Top movers (sector + thematic), by |change|
    movers = [t for c in ("Sector SPDR", "Thematic / Industry ETF") for t in byc.get(c, [])
              if t.get("change_pct") is not None]
    movers.sort(key=lambda t: abs(t.get("change_pct") or 0), reverse=True)
    for t in movers[:4]:
        items.append({"text": f"{t['display']} {_fmt_pct(t.get('change_pct'))}",
                      "tone": _tone_from_state(t.get("color_state"))})

    return items


def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
```

**Step 4: Run → pass**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests\test_ticker.py -q ; cd ..`
Expected: PASS

**Step 5: Commit**

```bash
git add webgui/pages/ticker.py webgui/tests/test_ticker.py
git commit -m "feat(ticker): pure live-item builders"
```

---

## Task 5: webgui ticker — render + marquee + Settings toggle + shell wiring

**Files:**
- Modify: `webgui/pages/ticker.py` (add `render_ticker` + the marquee CSS)
- Modify: `webgui/app_settings.py` (`ticker_enabled`, `ticker_speed`)
- Modify: `webgui/main.py` (call `ticker.render_ticker(active)` in `_layout`)
- Modify: `webgui/pages/settings.py` (toggle)
- Modify: `webgui/tests/test_no_inline_style.py` (guard `ticker.py`)
- Modify: `webgui/tests/test_ticker.py` (add a `build_scroll_children`-style pure test if you extract one)

**Step 1: Add settings defaults.** In `webgui/app_settings.py` `DEFAULTS`, add:
```python
    "ticker_enabled": True,
    "ticker_speed": 60,   # marquee duration seconds (higher = slower)
```

**Step 2: Add `render_ticker` + marquee CSS to `webgui/pages/ticker.py`.**

```python
import bus_client
import app_settings
from pages.ui_guard import guard
from nicegui import ui

# The ONE ui.add_css escape hatch for this component: a keyframe marquee animation
# (not expressible as a Tailwind utility). Everything else is Tailwind classes.
_TICKER_CSS = """
@keyframes mkt-marquee { from { transform: translateX(100%); } to { transform: translateX(-100%); } }
.mkt-ticker-scroll { display: inline-block; white-space: nowrap; will-change: transform;
  animation: mkt-marquee var(--mkt-dur, 60s) linear infinite; }
.mkt-ticker-wrap:hover .mkt-ticker-scroll { animation-play-state: paused; }
"""

VIEWS = ("market:dashboard", "sentiment:composite", "market:summary")


def render_ticker(active):
    """Fixed bottom marquee on every page (gated by the Settings toggle)."""
    if not app_settings.get("ticker_enabled"):
        return
    ui.add_css(_TICKER_CSS)
    speed = app_settings.get("ticker_speed") or 60
    with ui.footer().classes(
            "mkt-ticker-wrap bg-slate-950/95 border-t border-slate-700 "
            "h-8 px-3 flex items-center overflow-hidden z-[2200]"):
        scroll = ui.row().classes(f"mkt-ticker-scroll items-center gap-2")
        scroll.style(f"--mkt-dur: {speed}s")   # NOTE: see step 3 — replace with a class, no .style()

    state = {"versions": None}

    def _paint():
        dash = bus_client.read("market:dashboard")
        sent = bus_client.read("sentiment:composite")
        summ = bus_client.read("market:summary") or {}
        scroll.clear()
        with scroll:
            narrative = (summ.get("narrative") or "").strip()
            if narrative:
                ui.label(f"⚠ {narrative}").classes("text-amber-300 text-xs font-medium")
                ui.label("·").classes("text-slate-600 text-xs")
            items = ticker_items(dash, sent)
            if not items and not narrative:
                ui.label("Market data loading…").classes("text-slate-400 text-xs")
            for i, it in enumerate(items):
                ui.label(it["text"]).classes(f"text-xs {item_class(it['tone'])}")
                if i < len(items) - 1:
                    ui.label("·").classes("text-slate-600 text-xs")

    @guard
    def _poll():
        vers = bus_client.read_versions(VIEWS)
        if vers != state["versions"]:
            state["versions"] = vers
            _paint()

    _paint()
    ui.timer(4.0, _poll)
```

**Step 3: Remove the `.style()` (Tailwind-first).** The `--mkt-dur` CSS variable can't use
`.style()`. Instead set the duration via a small finite set of **speed classes** in
`_TICKER_CSS` (e.g. `.mkt-dur-slow{--mkt-dur:90s} .mkt-dur-med{--mkt-dur:60s}
.mkt-dur-fast{--mkt-dur:35s}`) and pick one with `.classes(speed_class(speed))` — add a pure
`speed_class(speed)` helper + test (maps a number to slow/med/fast). This keeps the page
`.style()`-free so `test_no_inline_style` passes. (Duration is the one genuinely-continuous
value, but a 3-bucket class is simplest and passes the guard; do NOT use `.style`.)

**Step 4: Wire into the shell.** In `webgui/main.py` `_layout`, just before `yield content`
(or right after the `ui.timer(2.0, _tick)`), add:
```python
    from pages import ticker
    ticker.render_ticker(active)
```
Give the content column bottom padding so the fixed footer never covers the last row: change
`with ui.column().classes("w-full p-4 gap-3") as content:` → add `pb-10` (`"w-full p-4 gap-3 pb-10"`).

**Step 5: Settings toggle.** In `webgui/pages/settings.py`, add a switch bound to
`ticker_enabled` (mirror the existing `alert_enabled` switch pattern — read `app_settings.get`,
write `app_settings.set` on change) and, optionally, a select for speed (Slow/Medium/Fast).

**Step 6: Guard.** Add `ticker.py` to `webgui/tests/test_no_inline_style.py` file list.
Confirm no `.style(` remains in `ticker.py` (use the speed-class approach from step 3).

**Step 7: Run the full webgui suite**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest -q ; cd ..`
Expected: PASS (all, incl. test_ticker + test_no_inline_style + test_settings if present).

**Step 8: Commit**

```bash
git add webgui/pages/ticker.py webgui/app_settings.py webgui/main.py webgui/pages/settings.py webgui/tests/
git commit -m "feat(ticker): fixed bottom marquee on every page + Settings toggle"
```

---

## Task 6: Live end-to-end + browser verify + docs

**Files:**
- Modify: `CLAUDE.md` (route/section note)

**Step 1: Redis-driven e2e.** With market_svc + sentiment_svc + proxy running, confirm the
summary publishes:
```
.venv\Scripts\python -c "import sys; sys.path.insert(0,'.'); from shared.bus import Bus; e=Bus().cache_get('cache:market:summary'); print(e.payload if e else 'none')"
```
Expected: a `{narrative: "...", ...}` (narrative non-empty IF an ANTHROPIC key is configured;
empty string otherwise — both are valid). Also confirm `cache:market:dashboard` +
`cache:sentiment:composite` are present so the live items populate.

**Step 2: Browser verify.** Start the `webgui` preview, open a couple of pages (e.g. `/` and
`/market`), and screenshot — confirm the ticker sits at the bottom, scrolls, shows the live
colored items (+ the narrative if a key is set), and doesn't cover content or the "?" fab.
Toggle it off in `/settings` and confirm it disappears. (If the screenshot tool times out on
a heavy page, verify on a light page or via `preview_inspect` on `.mkt-ticker-scroll`.)

**Step 3: Update `CLAUDE.md`.** Add a short "Market Summary Ticker — DONE" note under the
Market Dashboard section: the hybrid ticker (live rule-based items + scheduled Claude verdict),
`cache:market:summary`, the `market_svc` summary schedule (~20 min RTH), the `_layout` footer
marquee, and the Settings toggle. Link the design/plan docs.

**Step 4: Final suites green.**

Run: `.venv\Scripts\python -m pytest services\market_svc -q` → PASS
Run: `.venv\Scripts\python -m pytest shared\contracts -q` → PASS
Run: `cd webgui ; ..\.venv\Scripts\python -m pytest -q ; cd ..` → PASS

**Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(ticker): Market Summary Ticker shipped"
```

---

## Notes for the implementer

- Run service tests per-folder (`services\market_svc`), never `pytest services` over all.
- `shared.bus.Bus()` is fakeredis under pytest — no Redis needed for unit tests; inject a fake
  Claude client in `generate_summary` tests (never call the real API in tests).
- Tailwind-first: NO `.style()`. The marquee animation lives in the ONE `ui.add_css` escape
  hatch (`_TICKER_CSS`); the speed is a 3-bucket **class**, not an inline style. Item colors
  map from the finite `tone` set to fixed classes.
- All layers defensive: no Claude key → empty narrative → ticker shows live items; cold caches
  → "Market data loading…"; the scheduler branch and the webgui timer never raise out.
- Verify `repo_paths` exposes `SHARED_DIR` (options_svc uses it); if it's named `SHARED`, use
  that. Don't guess — grep `repo_paths.py`.
- `ui.footer()` renders once per page via `_layout`; it's fixed-position so DOM order doesn't
  matter, but keep it inside the `_layout` context so it's part of every page.
