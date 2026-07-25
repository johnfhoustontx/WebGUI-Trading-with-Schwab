# EOD Briefing + Live News + Notable Movers — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the useless 14:58 CT "close" Gamma briefing into a 15:15 CT **end-of-day
retrospective** (what the market did + what to prepare for next session), and enrich **all
four** briefings with **notable individual stock moves** + the **day's macro drivers** (live
web search).

**Architecture:** Two new shared assembly helpers in `services/options_svc/compute.py`
(`_notable_movers` from `cache:options:matrix` + `cache:market:dashboard` + today's flow
alerts; `_research_news` = a phase-1 Claude call with the **web-search server tool**). The
intraday path (`gamma_analyze`) becomes two-phase and gains optional `macro_drivers`/`movers`
fields; a new `eod_briefing()` two-phase path with its own forced `submit_eod` tool and its
own retrospective infographic serves the `close` slot. The scheduler moves `close`
14:58 → 15:15 CT and the handler branches `close` → `eod_briefing`. **The phone push is
already free** — `handlers.run_scheduled_gamma_analyze` already renders every slot's
infographic to PNG and pushes it via `push_notify.send_gamma_briefing`, so no push code is
needed; the EOD infographic simply flows through it.

**Tech Stack:** Python 3.11, `anthropic` SDK (lazy import, `claude-sonnet-5` via
`compute._ANALYZE_MODEL`), Redis/Memurai via `shared.bus`, SQLite `gex_history.db`, pytest.

---

## Ground rules (read before Task 1)

1. **`bus.cache_get()` returns a `CacheEnvelope`, NOT a dict.** Always
   `env.payload if env is not None else {}`. A fake-bus test returning bare dicts hides this
   bug and a best-effort `try/except` silently swallows it — that exact combination made a
   scheduled push never fire once already. Copy the `_snap_payload` idiom from
   `handlers.run_market_snapshot` (`handlers.py:1127`).
2. **Everything degrades, nothing raises.** Every new helper returns an empty/neutral value
   on ANY failure. The briefing must still render if news, movers, or the recap assembly all
   fail. Mirror the existing `_parse_analysis` / `_projection_brief` discipline.
3. **No whole-session grid decode.** `gex_history_db.load_date_with_grid` re-decoding a full
   session is a documented performance hotspot. Use `load_flow_series` (cheap: ts + spot +
   premiums) for the day's price path and `latest_flip` for the closing flip. Do NOT add a
   full-grid read.
4. **Lazy engine imports.** Keep `import gamma_tool as gt` and friends inside functions, as
   the surrounding code does.
5. **Run tests from the repo root**, one service at a time:
   `.venv\Scripts\python -m pytest services\options_svc -q`
   (never `pytest services` — it triggers the documented cross-app module collisions).
6. **Commit after each task.** Stage only the files that task touched — the working tree has
   unrelated parallel WIP (`webgui/pages/sentiment_*`, `docs/manuals/*`); a bare `git add -A`
   would sweep it in.

---

## Task 1: `_notable_movers()` — the day's notable individual stock moves

**Files:**
- Modify: `services/options_svc/compute.py` (add near `_projection_brief`, ~line 2451)
- Test: `services/options_svc/tests/test_compute.py`

**Context — the two data sources and their DIFFERENT day-% semantics (important):**
- `cache:market:dashboard` → `categories: [{"category", "tiles": [...]}]`, each tile
  `{display, description, category, last, change, change_pct, value_only, color_state,
  polarity}`. Its `change_pct` is the **true day change vs the prior close** (from the
  quote). ~48 macro tickers.
- `cache:options:matrix` → `rows: [...]` each `{symbol, spot, day_pct, trend_state,
  pc_ratio, net_prem_m, flip, gex_regime, n_signals, n_alerts, signal, _open_spot, …}`
  (see `services/options_svc/matrix.py:349`). Its `day_pct` is measured from the **first GEX
  snapshot of the session (~08:00 CT)**, i.e. an intraday session move, NOT vs the prior
  close. ~45 watchlist names.
- `cache:options:flow_alerts` → `{date, alerts: [{id, type, side, symbol, text, …}]}` —
  today's UOA + crossover events.

So: prefer the dashboard's `change_pct` when a symbol appears there; fall back to the
matrix's `day_pct` otherwise, and mark the row's `basis` so the prompt can label it honestly.
Skip `value_only` tiles (indices/internals like `$ADVN-$DECN` — a "move" is meaningless) and
skip the index/futures categories (we want **individual stocks**).

**Step 1: Write the failing tests**

```python
def test_notable_movers_prefers_dashboard_pct_and_sorts_by_magnitude():
    from services.options_svc import compute
    dashboard = {"categories": [
        {"category": "Top 10", "tiles": [
            {"display": "NVDA", "last": 100.0, "change_pct": -4.0, "category": "Top 10"},
            {"display": "AAPL", "last": 200.0, "change_pct": 1.0, "category": "Top 10"},
        ]},
        {"category": "Internals", "tiles": [
            {"display": "ADVN-DECN", "last": -465, "change_pct": 0, "value_only": True,
             "category": "Internals"},
        ]},
    ]}
    matrix = {"rows": [
        {"symbol": "NVDA", "spot": 100.0, "day_pct": -3.1, "n_alerts": 2},
        {"symbol": "MU", "spot": 50.0, "day_pct": 6.5, "n_alerts": 0},
    ]}
    alerts = {"alerts": [{"symbol": "NVDA", "type": "uoa", "side": "put"}]}
    out = compute._notable_movers(dashboard, matrix, alerts, limit=3)
    syms = [m["symbol"] for m in out]
    assert syms[0] == "MU"                    # |6.5| is the biggest move
    assert "NVDA" in syms
    assert "ADVN-DECN" not in syms            # value_only tile skipped
    nvda = next(m for m in out if m["symbol"] == "NVDA")
    assert nvda["day_pct"] == -4.0            # dashboard pct WINS over matrix day_pct
    assert nvda["basis"] == "prior_close"
    assert nvda["flow_alerts"] == 1           # cross-referenced
    mu = next(m for m in out if m["symbol"] == "MU")
    assert mu["basis"] == "session"           # matrix-only → intraday basis


def test_notable_movers_defensive_on_garbage():
    from services.options_svc import compute
    assert compute._notable_movers(None, None, None) == []
    assert compute._notable_movers({"categories": "nope"}, {"rows": None}, {}) == []
    # a row with no usable pct is dropped, not raised on
    assert compute._notable_movers({}, {"rows": [{"symbol": "X", "day_pct": None}]}, {}) == []
```

**Step 2: Run the tests to verify they fail**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -m pytest services/options_svc/tests/test_compute.py -k notable_movers -q
```
Expected: FAIL — `AttributeError: module ... has no attribute '_notable_movers'`.

**Step 3: Implement**

Add to `compute.py`:

```python
# Categories whose tiles are indices/futures/macro instruments, not individual
# stocks — excluded from "notable individual stock moves".
_MOVER_SKIP_CATEGORIES = frozenset({
    "Volatility", "Options Sentiment", "Internals", "Currency", "Cash Index",
    "Futures", "Fixed Income",
})
_MOVER_LIMIT = 6


def _notable_movers(dashboard, matrix, flow_alerts, limit: int = _MOVER_LIMIT) -> list:
    """The day's biggest individual-stock moves, for the briefing prompt.

    Merges two sources with DIFFERENT day-% semantics: the market dashboard's
    ``change_pct`` (true change vs the prior close — preferred) and the options
    matrix's ``day_pct`` (move since the ~08:00 CT session collection start). Each
    row carries ``basis`` so the prompt can label it honestly. Cross-references
    today's flow alerts so a mover that also printed unusual activity is flagged.

    Returns up to ``limit`` rows sorted by |move| desc. Pure over already-fetched
    cache payloads; fully defensive → ``[]``."""
    out = {}
    try:
        # Flow-alert counts per symbol (today's UOA + crossover events).
        counts = {}
        for a in ((flow_alerts or {}).get("alerts") or []):
            sym = str((a or {}).get("symbol") or "").lstrip("$").upper()
            if sym:
                counts[sym] = counts.get(sym, 0) + 1

        for cat in ((dashboard or {}).get("categories") or []):
            if not isinstance(cat, dict):
                continue
            if str(cat.get("category") or "") in _MOVER_SKIP_CATEGORIES:
                continue
            for t in (cat.get("tiles") or []):
                if not isinstance(t, dict) or t.get("value_only"):
                    continue
                pct, sym = t.get("change_pct"), str(t.get("display") or "").strip()
                if not sym or not isinstance(pct, (int, float)) or isinstance(pct, bool):
                    continue
                key = sym.lstrip("$").upper()
                out[key] = {"symbol": sym, "day_pct": round(float(pct), 2),
                            "last": t.get("last"), "basis": "prior_close",
                            "flow_alerts": counts.get(key, 0)}

        for r in ((matrix or {}).get("rows") or []):
            if not isinstance(r, dict):
                continue
            sym = str(r.get("symbol") or "").strip()
            key = sym.lstrip("$").upper()
            if not sym or key in out:      # dashboard's prior-close pct wins
                continue
            pct = r.get("day_pct")
            if not isinstance(pct, (int, float)) or isinstance(pct, bool):
                continue
            out[key] = {"symbol": sym, "day_pct": round(float(pct), 2),
                        "last": r.get("spot"), "basis": "session",
                        "flow_alerts": counts.get(key, int(r.get("n_alerts") or 0))}

        rows = sorted(out.values(), key=lambda m: abs(m["day_pct"]), reverse=True)
        return rows[:max(0, int(limit))]
    except Exception:
        log.debug("_notable_movers failed", exc_info=True)
        return []


def _movers_prompt_block(movers) -> str:
    """Render movers for the model prompt, labeling each move's basis honestly."""
    if not movers:
        return ""
    lines = []
    for m in movers:
        basis = "vs prior close" if m.get("basis") == "prior_close" else "since the open"
        bit = f"{m['symbol']} {m['day_pct']:+.2f}% ({basis})"
        if m.get("flow_alerts"):
            bit += f" — {m['flow_alerts']} unusual-flow alert(s) today"
        lines.append(bit)
    return "NOTABLE INDIVIDUAL STOCK MOVES (code-computed, use verbatim):\n" + \
           "\n".join(f"- {x}" for x in lines)
```

**Step 4: Run the tests to verify they pass**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -m pytest services/options_svc/tests/test_compute.py -k notable_movers -q
```
Expected: 2 passed.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(briefing): _notable_movers — day's biggest individual stock moves"
```

---

## Task 2: `_research_news()` — the day's macro drivers via web search

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_compute.py`

**The web-search spec was researched against the claude-api reference — these are the
verified facts. Do not re-derive or substitute your own strings:**

| Item | Verified value |
|---|---|
| Tool `type` | `web_search_20260318` (latest; `web_search_20260209` / `web_search_20250305` also exist) |
| Tool `name` | `web_search` |
| Beta header / `betas=[…]` | **None required** — web search is GA |
| Model support | Per-model support is not enumerated on the tool page, BUT the API reference's tool-version table documents **`web_search_20260209` on Claude Sonnet 4.6** (and Opus 4.8/4.7/4.6). Support on **`claude-sonnet-5`** (our `_ANALYZE_MODEL`) is **undocumented.** → **Use `claude-sonnet-4-6` + `web_search_20260209` for the news phase** (user directive: Sonnet 4.6, no Opus). |
| Forced `tool_choice` + a client tool in one turn | **Impossible.** If the model calls web search and a client tool in the same parallel group, the API returns `stop_reason: "tool_use"` and **defers the search**. Two calls are required. |
| Errors | Arrive **inside a 200 response** as a `web_search_tool_result` block whose `content` is a dict with `error_code` (`too_many_requests`, `max_uses_exceeded`, `query_too_long`, `unavailable`, …). A `try/except` alone will NOT catch them. |
| Pricing | **$10 per 1,000 searches** + normal tokens; errors are not charged. At 4 briefings/day ≈ **$0.04/day**. |
| Sources | `citations` on the text blocks: `{type: "web_search_result_location", url, title, cited_text}`. |

**Two consequences for the code below:**
1. **Use a separate `_NEWS_MODEL = "claude-sonnet-4-6"`** rather than `_ANALYZE_MODEL`
   (`claude-sonnet-5`, whose web-search support is undocumented). Sonnet 4.6 + tool version
   `web_search_20260209` is the documented pairing. **Do not use an Opus model here** — an
   explicit user directive; ask before changing it. The existing `_ANALYZE_MODEL` (the render
   phase) is **left untouched** — it is already a Sonnet and changing it would alter the three
   intraday briefings' behavior, which is out of scope for this feature.
2. **Detect the in-response error block**, not just exceptions — otherwise a rate-limited
   search silently yields the model's un-searched guesswork, which is *worse* than no news
   (fabricated headlines in a briefing). On an error block → return `[]`.

**Also watch:** on `web_search_20260209`+ the `allowed_callers` default is documented as
`code_execution`. Since we call the tool **directly** (no code-execution tool), pass
`"allowed_callers": ["direct"]` explicitly and confirm in the Step 5 probe that a search
actually runs (a `server_tool_use` block appears) rather than silently never firing.

**Why this is a SEPARATE call from the render:** confirmed above — the render phase forces
`tool_choice={"type": "tool", "name": "submit_analysis"}`, which cannot coexist with a
server-side search in one turn. The two-phase design stands.

**Step 1: Write the failing tests** (the network is never touched — a fake client throughout)

```python
class _FakeBlock:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeNewsClient:
    """Minimal stand-in for anthropic.Anthropic for the news phase."""
    def __init__(self, blocks=None, raise_exc=None):
        self._blocks, self._raise = blocks or [], raise_exc
        self.calls = []
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                if outer._raise:
                    raise outer._raise
                return _FakeBlock(content=outer._blocks)
        self.messages = _Messages()


def test_research_news_returns_headline_lines():
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[
        _FakeBlock(type="text", text="- Fed held rates steady\n- CPI came in cool\n"),
    ])
    out = compute._research_news("close", "SPX closed -0.8%", client=client)
    assert out and any("Fed" in line for line in out)
    # The web-search tool must actually be offered to the model.
    assert client.calls and client.calls[0].get("tools")


def test_research_news_degrades_to_empty():
    from services.options_svc import compute
    # No client (no API key) → [] and NO exception.
    assert compute._research_news("close", "ctx", client=None) == []
    # API error → []
    assert compute._research_news(
        "close", "ctx", client=_FakeNewsClient(raise_exc=RuntimeError("boom"))) == []
    # No text blocks → []
    assert compute._research_news("close", "ctx", client=_FakeNewsClient(blocks=[])) == []


def test_research_news_drops_result_when_search_itself_errored():
    """A failed search returns HTTP 200 with an error block — the model then answers
    from memory. Returning that text would put FABRICATED headlines in a briefing,
    which is worse than no news. Must yield []."""
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[
        _FakeBlock(type="web_search_tool_result", tool_use_id="srvtoolu_1",
                   content={"type": "web_search_tool_result_error",
                            "error_code": "too_many_requests"}),
        _FakeBlock(type="text", text="- Stocks probably moved on rate expectations"),
    ])
    assert compute._research_news("close", "ctx", client=client) == []


def test_research_news_offers_tool_with_direct_caller():
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[_FakeBlock(type="text", text="- CPI cool")])
    compute._research_news("close", "ctx", client=client)
    tool = client.calls[0]["tools"][0]
    assert tool["type"].startswith("web_search_")
    assert tool["name"] == "web_search"
    # allowed_callers defaults to code_execution on v20260209+ — we call directly.
    assert tool["allowed_callers"] == ["direct"]
    assert "betas" not in client.calls[0]        # web search is GA


def test_news_prompt_block_empty_when_no_news():
    from services.options_svc import compute
    assert compute._news_prompt_block([]) == ""
    assert "DRIVERS" in compute._news_prompt_block(["Fed held rates"]).upper()
```

**Step 2: Run to verify they fail**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -m pytest services/options_svc/tests/test_compute.py -k "research_news or news_prompt" -q
```
Expected: FAIL — `_research_news` does not exist.

**Step 3: Implement.**

```python
_NEWS_MAX_TOKENS = 700
_NEWS_MAX_LINES = 6
# Web search is GA — no beta header. `allowed_callers` is set EXPLICITLY because on
# v20260209+ it defaults to code_execution, and we call the tool directly.
_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search",
                    "max_uses": 3, "allowed_callers": ["direct"]}
# The news phase runs on Sonnet 4.6 — the documented pairing for web_search_20260209.
# NOT _ANALYZE_MODEL (claude-sonnet-5), whose web-search support is undocumented, and
# NOT an Opus model (explicit user directive — ask before changing).
_NEWS_MODEL = "claude-sonnet-4-6"
# Search-failure codes that arrive INSIDE a 200 response (see the spec table).
_NEWS_ERROR_BLOCK = "web_search_tool_result"

_NEWS_SYSTEM = (
    "You are a market-news researcher. Search the web for the concrete macro and "
    "earnings news that actually moved US equities TODAY (Fed/rates, CPI/PPI/jobs, "
    "major earnings, geopolitics). Reply with a short plain list, one driver per line, "
    "prefixed by '- ', each naming the event and its market effect in ONE clause. Cite "
    "nothing else, add no preamble, no disclaimers. If asked for the next session, also "
    "list tomorrow's scheduled economic releases and notable earnings."
)


def _research_news(label: str, context: str = "", client=None, eod: bool = False) -> list:
    """Search the web for the day's macro drivers → a short list of driver lines.

    Phase 1 of a briefing: a SEPARATE Claude call with the web-search server tool
    (the render phase forces `submit_*`, which leaves no turn for searching). When
    ``eod`` is set, also asks for the NEXT session's scheduled releases/earnings.

    Fully guarded → ``[]`` on no key / unsupported tool / API error / empty reply, so
    a briefing always renders (app-data-only). ``client`` is injected in tests."""
    client = client or _make_analyze_client()
    if client is None:
        return []
    ask = ("Today's US session just closed. " if eod else "The US session is in progress. ")
    ask += "What news drove the tape today?"
    if eod:
        ask += (" Also list the scheduled economic releases and notable earnings for the "
                "NEXT trading session.")
    if context:
        ask += f"\n\nMarket context (already computed, do not re-derive):\n{context}"
    try:
        _count_anthropic_call()
        resp = client.messages.create(
            model=_NEWS_MODEL,
            max_tokens=_NEWS_MAX_TOKENS,
            system=_NEWS_SYSTEM,
            tools=[_WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": ask}],
        )
    except Exception:
        log.warning("news research (%s) failed — briefing degrades to app data only",
                    label, exc_info=True)
        return []
    lines = []
    try:
        blocks = getattr(resp, "content", None) or []
        # A FAILED search still returns HTTP 200: an error block, then the model
        # answering from memory. Publishing that text would put fabricated headlines
        # in the briefing — strictly worse than no news. Bail out entirely.
        for b in blocks:
            if getattr(b, "type", None) != _NEWS_ERROR_BLOCK:
                continue
            c = getattr(b, "content", None)
            if isinstance(c, dict) and c.get("error_code"):
                log.warning("news research (%s): web search errored (%s) — no news",
                            label, c.get("error_code"))
                return []
        for b in blocks:
            if getattr(b, "type", None) != "text":
                continue          # skip server_tool_use / web_search_tool_result blocks
            for raw in (getattr(b, "text", "") or "").splitlines():
                s = raw.strip().lstrip("-•* ").strip()
                if s:
                    lines.append(s)
    except Exception:
        log.debug("news parse failed", exc_info=True)
        return []
    return lines[:_NEWS_MAX_LINES]


def _news_prompt_block(news) -> str:
    if not news:
        return ""
    return ("MACRO DRIVERS / NEWS (searched live — attribute the tape to these):\n"
            + "\n".join(f"- {n}" for n in news))
```

**Step 4: Run to verify they pass** (same command). Expected: 5 passed.

**Step 5: Live-probe the tool — MANDATORY.** The unit tests use a fake client, so they prove
nothing about the real API, and the two open questions (does `claude-sonnet-5`/`_NEWS_MODEL`
support the tool? does a search actually fire with `allowed_callers: ["direct"]`?) can only be
answered live. Probe the raw response, not just the parsed list:

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -c "
import sys,pathlib; sys.path.insert(0,str(pathlib.Path('.').resolve()))
from services.options_svc import compute as c
cl = c._make_analyze_client()
r = cl.messages.create(model=c._NEWS_MODEL, max_tokens=c._NEWS_MAX_TOKENS,
                       system=c._NEWS_SYSTEM, tools=[c._WEB_SEARCH_TOOL],
                       messages=[{'role':'user','content':'What news drove US equities today?'}])
print('BLOCK TYPES:', [getattr(b,'type',None) for b in (r.content or [])])
print('PARSED     :', c._research_news('probe', 'SPX -0.8%', eod=True))
"
```
**Verify all three:**
1. `BLOCK TYPES` contains **`server_tool_use`** — proof a search actually ran. If it's only
   `text`, the search never fired (likely the `allowed_callers` trap) and the "news" is the
   model's memory — **fix before proceeding**.
2. No `web_search_tool_result_error`.
3. `PARSED` is a non-empty list of real, dated-sounding headlines.

**If the call 400s on the model or the tool version**, try the adjacent documented
combination (`web_search_20260318`, still on `claude-sonnet-4-6`) and record what actually
worked in the constant's comment with the live-verified date. **Do NOT switch to an Opus
model to make it work — stop and ask the user first** (explicit directive).

**Step 6: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(briefing): _research_news — live macro drivers via web search"
```

---

## Task 3: `_eod_session_recap()` — what the market actually did today

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_compute.py`

**Context:** `gex_history_db.load_flow_series(conn, symbol, d)` yields
`(ts, spot, call_vol, put_vol, call_prem, put_prem)` per snapshot — cheap (no grid decode).
That gives the session **open / high / low / close** spot per index. `latest_flip(conn,
symbol, d)` gives the closing gamma flip. The closing **call/put walls** are already computed
by the briefing itself (`_gamma_blocks_for` off the live chain) — pass them in rather than
re-reading. Session date comes from `scheduler.active_session_date()`.

**Step 1: Write the failing tests**

```python
def test_session_path_from_series():
    from services.options_svc import compute
    series = [(1, 100.0, 0, 0, 0, 0), (2, 104.0, 0, 0, 0, 0),
              (3, 98.0, 0, 0, 0, 0), (4, 101.0, 0, 0, 0, 0)]
    p = compute._session_path(series)
    assert (p["open"], p["high"], p["low"], p["close"]) == (100.0, 104.0, 98.0, 101.0)
    assert p["day_pct"] == 1.0        # 100 -> 101
    assert compute._session_path([]) == {}
    assert compute._session_path(None) == {}


def test_level_verdict_held_vs_broke():
    from services.options_svc import compute
    # Closed above a flip it traded below at some point → reclaimed.
    assert "reclaim" in compute._level_verdict(
        {"open": 99.0, "high": 105.0, "low": 98.0, "close": 104.0}, 100.0, "gamma flip").lower()
    # Never reached the level → untested.
    assert "did not" in compute._level_verdict(
        {"open": 90.0, "high": 95.0, "low": 89.0, "close": 94.0}, 120.0, "call wall").lower()
    # Missing level → empty string, no raise.
    assert compute._level_verdict({"open": 1.0}, None, "flip") == ""


def test_eod_recap_prompt_block_is_defensive():
    from services.options_svc import compute
    # No data at all → empty string, never raises.
    assert compute._eod_recap_prompt_block({}) == ""
    block = compute._eod_recap_prompt_block({
        "$SPX": {"path": {"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
                          "day_pct": 4.0},
                 "flip": 101.0, "call_wall": 106.0, "put_wall": 98.0},
    })
    assert "$SPX" in block and "104" in block
```

**Step 2: Run to verify they fail.** Expected: FAIL (functions missing).

**Step 3: Implement**

```python
def _session_path(series) -> dict:
    """open/high/low/close + day % from a flow series' spot column. {} if unusable."""
    try:
        spots = [r[1] for r in (series or [])
                 if len(r) > 1 and isinstance(r[1], (int, float)) and r[1] > 0]
        if not spots:
            return {}
        o, c = float(spots[0]), float(spots[-1])
        return {"open": round(o, 2), "high": round(max(spots), 2),
                "low": round(min(spots), 2), "close": round(c, 2),
                "day_pct": round((c - o) / o * 100.0, 2) if o else None}
    except Exception:
        return {}


def _level_verdict(path, level, name: str) -> str:
    """Plain-English: did price hold / break / reclaim / never test this level?"""
    try:
        if not path or not isinstance(level, (int, float)) or isinstance(level, bool):
            return ""
        hi, lo, close = path.get("high"), path.get("low"), path.get("close")
        if not all(isinstance(v, (int, float)) for v in (hi, lo, close)):
            return ""
        if lo > level:
            return f"stayed entirely above the {name} ({level:g}) — never tested"
        if hi < level:
            return f"did not reach the {name} ({level:g}) all session"
        if close >= level:
            return f"traded below then reclaimed the {name} ({level:g}), closing above"
        return f"lost the {name} ({level:g}) and closed below it"
    except Exception:
        return ""


def _eod_recap_prompt_block(recap) -> str:
    """Render the per-index session recap for the model prompt. '' when empty."""
    if not recap:
        return ""
    out = []
    for sym, d in (recap or {}).items():
        try:
            p = (d or {}).get("path") or {}
            if not p:
                continue
            bits = [f"{sym}: open {p.get('open')} / high {p.get('high')} / "
                    f"low {p.get('low')} / close {p.get('close')} "
                    f"({(p.get('day_pct') or 0):+.2f}%)"]
            for key, name in (("flip", "gamma flip"), ("call_wall", "call wall"),
                              ("put_wall", "put wall")):
                v = _level_verdict(p, d.get(key), name)
                if v:
                    bits.append(f"  · {v}")
            out.append("\n".join(bits))
        except Exception:
            continue
    if not out:
        return ""
    return ("TODAY'S SESSION PATH + LEVELS (code-computed, use verbatim):\n"
            + "\n".join(out))


def _eod_session_recap(levels_by_sym) -> dict:
    """Per-index session recap: today's spot path + the CLOSING key levels.

    ``levels_by_sym`` = ``{symbol: {"flip", "call_wall", "put_wall"}}`` computed by the
    caller off the live chain (do NOT re-read the grid — see the perf note). The spot
    path comes from the cheap flow series. Defensive → ``{}``."""
    try:
        import gex_history_db as gh

        from services.options_svc import scheduler as _sched
        d = _sched.active_session_date()
        out, conn = {}, None
        try:
            conn = gh.connect(read_only=True)
            for sym, lv in (levels_by_sym or {}).items():
                try:
                    series = gh.load_flow_series(conn, sym, d)
                    path = _session_path(series)
                    if not path:
                        continue
                    row = {"path": path}
                    row.update({k: (lv or {}).get(k)
                                for k in ("flip", "call_wall", "put_wall")})
                    if row.get("flip") is None:
                        row["flip"] = gh.latest_flip(conn, sym, d)
                    out[sym] = row
                except Exception:
                    continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        return out
    except Exception:
        log.debug("_eod_session_recap failed", exc_info=True)
        return {}
```

**Note for the implementer:** verify `gex_history_db.connect`'s real signature and
`latest_flip`'s argument order before finalizing — read `options-scanner/gex_history_db.py`.
Adjust the call, not the contract.

**Step 4: Run to verify they pass.** Expected: 3 passed.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(briefing): _eod_session_recap — today's path vs the key levels"
```

---

## Task 4: `submit_eod` tool + `_parse_eod` + `eod_briefing()`

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_compute.py`

**Context:** model `eod_briefing()` on `gamma_analyze()` (`compute.py:3035`) — same chain
fetch, same `_make_analyze_client`, same degrade-to-readable-HTML on every failure surface,
same code-authoritative EM override. It differs in: the system prompt (retrospective, not
forward), the forced tool (`submit_eod`), the prompt extras (recap + movers + news instead of
the forward projection), and the renderer (Task 5).

**Step 1: Write the failing tests**

```python
def test_parse_eod_is_total_over_garbage():
    from services.options_svc import compute
    assert compute._parse_eod(None) is None
    assert compute._parse_eod({}) is None
    d = compute._parse_eod({
        "regime": "Risk-off unwind", "bias": -30, "headline": "Sellers won the day",
        "narrative": "n", "why": "w",
        "macro_drivers": ["Fed held", 5],           # non-str dropped
        "movers": [{"symbol": "MU", "move": "+6.5%", "note": "squeeze"}, "junk"],
        "next_session": {"levels": "watch 5900", "posture": "cautious"},
        "indices": [{"symbol": "$SPX", "recap": "faded from the open"}],
    })
    assert d["regime"] == "Risk-off unwind" and d["bias"] == -30
    assert d["macro_drivers"] == ["Fed held"]
    assert len(d["movers"]) == 1 and d["movers"][0]["symbol"] == "MU"
    assert d["indices"][0]["symbol"] == "$SPX"
    assert d["next_session"]["posture"] == "cautious"


def test_eod_briefing_degrades_without_chains(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain", lambda s: None)
    res = compute.eod_briefing(client=object())
    assert "html" in res and "analysis" not in res       # degraded → no push
    assert "could not fetch" in res["html"].lower() or "no " in res["html"].lower()


def test_eod_briefing_renders_and_overrides_em(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain",
                        lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 4.2)
    monkeypatch.setattr(compute, "_eod_session_recap", lambda lv: {})
    monkeypatch.setattr(compute, "_research_news", lambda *a, **k: ["Fed held rates"])
    monkeypatch.setattr(compute, "_notable_movers", lambda *a, **k: [])

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                assert kw["tool_choice"]["name"] == "submit_eod"
                blk = type("B", (), {"type": "tool_use", "name": "submit_eod",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": [{"symbol": "$SPX",
                                                            "expected_move": 999}]}})()
                return type("R", (), {"content": [blk]})()
    res = compute.eod_briefing(client=_C())
    assert res.get("analysis")
    assert res["analysis"]["indices"][0]["expected_move"] == 4.2   # code-authoritative
```

**Step 2: Run to verify they fail.**

**Step 3: Implement.** Add `_EOD_SYSTEM`, `_EOD_TOOL`, `_parse_eod`, `eod_briefing`.

`_EOD_SYSTEM` must state, in this spirit:
- "The US cash session has CLOSED. Write a RETROSPECTIVE, not a forward intraday playbook.
  Never advise intraday entries for a session that is over."
- "Say what the market DID today, WHY (use the supplied macro drivers), which key levels
  held or broke (use the supplied session path verbatim), and which individual names moved."
- "Then give `next_session`: the levels that matter tomorrow (today's closing walls/flip
  persist overnight because open interest does), the expected-move band, tomorrow's scheduled
  catalysts, and the posture to carry in."
- Keep the existing house rules: copy computed levels exactly, never invent numbers, no
  disclaimers, reader-first framing.

`_EOD_TOOL` input schema: `regime`, `bias`, `bias_label`, `headline`, `narrative`, `why`
(all required as today), plus:
- `macro_drivers`: array of short strings — the day's actual drivers.
- `movers`: array of `{symbol, move, note}` — notable individual names.
- `indices`: array of `{symbol, spot, gamma_flip, call_wall, put_wall, max_pain,
  expected_move, pc_ratio, recap}` — where **`recap`** replaces `note`/`close_outlook`/
  `what_if` ("what this index did today and where it closed vs its levels").
- `next_session`: `{levels, expected_move_note, catalysts, posture}` — all strings.

`_parse_eod` mirrors `_parse_analysis`: total over adversarial input, coercing/dropping
bad entries, returning `None` when the essentials are absent.

`eod_briefing(client=None, label=None)` flow:
1. Fetch the three chains + `_gamma_blocks_for` + `_session_expected_move` (copy
   `gamma_analyze`'s loop verbatim, including its all-chains-None degrade page — reword the
   message for EOD).
2. Build `levels_by_sym` from the blocks (flip + call/put wall via the same accessor
   `gamma_analyze`'s prompt builder uses) → `_eod_session_recap(levels_by_sym)`.
3. `news = _research_news(label or "close", context=<one-line index summary>, eod=True)`.
4. `movers = _notable_movers(dashboard, matrix, flow_alerts)` — read those three caches here
   via a module-level bus handle **using `.payload`** (ground rule 1); wrap in `try/except` →
   `{}`.
5. Prompt = `gt.build_summary_prompt_bundled(...)` + `_eod_recap_prompt_block(recap)` +
   `_movers_prompt_block(movers)` + `_news_prompt_block(news)`.
6. Forced `submit_eod` call → `_parse_eod` → EM override → `eod_infographic_html` (Task 5).
7. Return `{"html", "prompt", "analysis"}`, degrading to a readable page at every failure.

**Step 4: Run to verify they pass.** Expected: 3 passed.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(briefing): eod_briefing — retrospective close briefing via submit_eod"
```

---

## Task 5: EOD infographic + shared movers/macro sections

**Files:**
- Modify: `services/options_svc/compute.py` (near `analyze_infographic_html`, ~line 2961)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Write the failing tests**

```python
def test_shared_sections_render_and_are_omitted_when_empty():
    from services.options_svc import compute
    assert compute._movers_html([]) == ""
    assert compute._macro_html([]) == ""
    h = compute._movers_html([{"symbol": "MU", "day_pct": 6.5, "basis": "session",
                               "flow_alerts": 2}])
    assert "MU" in h and "6.5" in h
    assert "Fed" in compute._macro_html(["Fed held rates"])


def test_movers_html_escapes_and_colors():
    from services.options_svc import compute
    h = compute._movers_html([{"symbol": "<b>X</b>", "day_pct": -3.0, "basis": "prior_close"}])
    assert "<b>X</b>" not in h and "&lt;b&gt;" in h      # escaped
    assert "-3.0" in h or "-3.00" in h


def test_eod_infographic_includes_recap_and_next_session():
    from services.options_svc import compute
    html = compute.eod_infographic_html({
        "regime": "Risk-off unwind", "bias": -40, "bias_label": "Bearish",
        "headline": "Sellers controlled the tape", "narrative": "n", "why": "w",
        "macro_drivers": ["Fed held rates"],
        "movers": [{"symbol": "MU", "day_pct": 6.5, "basis": "session"}],
        "indices": [{"symbol": "$SPX", "spot": 100.0, "gamma_flip": 101.0,
                     "recap": "lost the flip and closed below"}],
        "next_session": {"levels": "watch 5900", "posture": "cautious",
                         "catalysts": "CPI 7:30 CT", "expected_move_note": "±35"},
    }, "sub")
    for needle in ("Sellers controlled", "$SPX", "lost the flip", "Fed held rates",
                   "MU", "next session", "CPI"):
        assert needle.lower() in html.lower()


def test_analyze_infographic_still_renders_without_new_fields():
    from services.options_svc import compute
    html = compute.analyze_infographic_html(
        {"regime": "r", "bias": 0, "headline": "h", "narrative": "n", "why": "w",
         "indices": [{"symbol": "SPY"}]}, "sub")
    assert "SPY" in html      # no regression when macro_drivers/movers are absent
```

**Step 2: Run to verify they fail.**

**Step 3: Implement**
- `_movers_html(movers)` — a compact chip strip, green/red by sign, `basis` shown as
  "vs prior close" / "since open", flow-alert count as a badge. `""` when empty.
  **`_h.escape` every model/symbol string** (this is raw HTML, the documented out-of-scope
  path for Tailwind — inline styles are correct here, matching `_metric_tiles_html`).
- `_macro_html(drivers)` — a "What's driving the tape" bullet list. `""` when empty.
- Append both to `analyze_infographic_html` (guarded by presence → zero regression for the
  intraday briefings that lack them).
- `eod_infographic_html(data, subtitle)` — EOD layout: recap banner (regime + bias meter,
  reusing `_bias_meter_html`) → headline/narrative → per-index **recap** card (reuse
  `_ladder_svg` + `_metric_tiles_html`; render `recap` where the intraday shows
  `note`/`what_if`) → `_movers_html` → `_macro_html` → a **"Prepare for the next session"**
  block (levels / expected move / catalysts / posture) → the "Why" section.

**Step 4: Run to verify they pass.** Expected: 4 passed.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(briefing): EOD infographic + shared movers/macro-driver sections"
```

---

## Task 6: Enrich the three intraday briefings

**Files:**
- Modify: `services/options_svc/compute.py` (`_ANALYZE_SYSTEM`, `_ANALYZE_TOOL`,
  `_parse_analysis`, `gamma_analyze`)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Write the failing tests**

```python
def test_analyze_tool_accepts_optional_macro_and_movers():
    from services.options_svc import compute
    props = compute._ANALYZE_TOOL["input_schema"]["properties"]
    assert "macro_drivers" in props and "movers" in props
    # still NOT required — a model reply without them must parse
    assert "macro_drivers" not in compute._ANALYZE_TOOL["input_schema"]["required"]
    d = compute._parse_analysis({"regime": "r", "bias": 0, "headline": "h",
                                 "narrative": "n", "why": "w", "indices": [],
                                 "macro_drivers": ["CPI cool"], "movers": []})
    assert d["macro_drivers"] == ["CPI cool"]


def test_gamma_analyze_threads_news_and_movers_into_prompt(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain",
                        lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)
    monkeypatch.setattr(compute, "_research_news", lambda *a, **k: ["Fed held rates"])
    monkeypatch.setattr(compute, "_notable_movers",
                        lambda *a, **k: [{"symbol": "MU", "day_pct": 6.5,
                                          "basis": "session", "flow_alerts": 0}])
    seen = {}

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                seen["prompt"] = kw["messages"][0]["content"]
                blk = type("B", (), {"type": "tool_use", "name": "submit_analysis",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": []}})()
                return type("R", (), {"content": [blk]})()
    compute.gamma_analyze(client=_C())
    assert "Fed held rates" in seen["prompt"]
    assert "MU" in seen["prompt"]


def test_gamma_analyze_survives_news_and_movers_failure(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain",
                        lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)
    def _boom(*a, **k):
        raise RuntimeError("news down")
    monkeypatch.setattr(compute, "_research_news", _boom)
    monkeypatch.setattr(compute, "_notable_movers", _boom)

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                blk = type("B", (), {"type": "tool_use", "name": "submit_analysis",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": []}})()
                return type("R", (), {"content": [blk]})()
    res = compute.gamma_analyze(client=_C())
    assert res.get("analysis")      # briefing still renders
```

**Step 2: Run to verify they fail.**

**Step 3: Implement**
- `_ANALYZE_TOOL`: add optional `macro_drivers` (array of string) + `movers` (array of
  `{symbol, move, note}`). **Do not add them to `required`** — an older/terser reply must
  still parse.
- `_ANALYZE_SYSTEM`: add one sentence — use the supplied MACRO DRIVERS to explain *why* the
  tape is acting this way in `why`, and surface the supplied NOTABLE MOVES in `movers`
  (copy the computed percentages, don't invent).
- `_parse_analysis`: carry `macro_drivers` + `movers` through with the same defensive
  coercion as `_parse_eod` (share a helper if it reads cleanly — DRY).
- `gamma_analyze`: after the existing forward-projection block, append
  `_movers_prompt_block(...)` + `_news_prompt_block(...)`, **each in its own `try/except`**
  so a news/cache failure can never break the briefing (the third test pins this).

**Step 4: Run to verify they pass.** Expected: 3 passed.

**Step 5: Full-module regression**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -m pytest services/options_svc/tests/test_compute.py -q
```
Expected: all pass (2 pre-existing `test_expected_move` date-relative failures may appear —
they are the documented baseline, do NOT "fix" them).

**Step 6: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(briefing): intraday briefings gain macro drivers + notable movers"
```

---

## Task 7: Move the close slot to 15:15 CT + branch the handler

**Files:**
- Modify: `services/options_svc/scheduler.py:223-230` (`_ANALYZE_SLOTS`)
- Modify: `services/options_svc/handlers.py:1048` (`run_scheduled_gamma_analyze`)
- Test: `services/options_svc/tests/test_scheduler.py`,
  `services/options_svc/tests/test_handlers.py`

**Step 1: Write the failing tests**

```python
# test_scheduler.py
def test_close_analyze_slot_is_after_the_cash_close():
    from services.options_svc import scheduler as s
    assert s._ANALYZE_SLOTS["close"] == (15, 15)   # was (14, 58) — pre-close, useless


def test_close_slot_fires_at_1515_not_1458():
    import datetime as dt
    from zoneinfo import ZoneInfo
    from services.options_svc import scheduler as s
    ct = ZoneInfo("America/Chicago")
    # A Tuesday.
    assert s.analyze_slot_due(dt.datetime(2026, 7, 21, 14, 58, tzinfo=ct), set()) != "close"
    assert s.analyze_slot_due(dt.datetime(2026, 7, 21, 15, 15, tzinfo=ct), set()) == "close"


# test_handlers.py
def test_close_slot_routes_to_eod_briefing(monkeypatch):
    from services.options_svc import handlers
    calls = []
    monkeypatch.setattr(handlers.compute, "eod_briefing",
                        lambda **kw: calls.append("eod") or {"html": "h", "analysis": {"a": 1}})
    monkeypatch.setattr(handlers.compute, "gamma_analyze",
                        lambda **kw: calls.append("intraday") or {"html": "h"})
    monkeypatch.setattr(handlers, "_persist_briefing", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "publish_gamma_briefing_index", lambda b: None)
    monkeypatch.setattr(handlers.push_notify, "send_gamma_briefing", lambda *a, **k: True)

    class _Bus:
        def cache_set(self, *a, **k): return 1
        def publish(self, *a, **k): pass
    handlers.run_scheduled_gamma_analyze(_Bus(), "close")
    handlers.run_scheduled_gamma_analyze(_Bus(), "midday")
    assert calls == ["eod", "intraday"]
```

**Step 2: Run to verify they fail.**

**Step 3: Implement**
- `scheduler._ANALYZE_SLOTS["close"]`: `(14, 58)` → `(15, 15)`. Update its comment:
  `# 16:15 ET — EOD retrospective (after the 15:00 CT cash close)`.
- `handlers.run_scheduled_gamma_analyze`: replace the single
  `res = compute.gamma_analyze(label=label)` with
  ```python
  res = (compute.eod_briefing(label=label) if slot == "close"
         else compute.gamma_analyze(label=label))
  ```
  Everything downstream (cache_set / publish / `_persist_briefing` /
  `publish_gamma_briefing_index` / `send_gamma_briefing`) is **unchanged** — the EOD result
  has the same `{"html", "analysis"}` shape, so the PNG push works as-is. Update the
  docstring to say `close` is the EOD retrospective.

**Step 4: Run to verify they pass.**

**Step 5: Check for other hardcoded references to the old time / slot semantics**

```bash
cd "D:/WebGUI Trading with Schwab" && grep -rn "14, 58\|14:58\|At close\|close_outlook" services/ webgui/ --include=*.py | grep -v tests
```
Fix any stale label (e.g. `push_notify._BRIEFING_SLOT_LABELS["close"]` should read
`"EOD recap"`, and `handlers.ANALYZE_SLOT_TITLES["close"]` likewise). `close_outlook`
remains valid for the three intraday slots — leave it.

**Step 6: Full service suite**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -m pytest services/options_svc -q
```
Expected: all pass except the 2 documented `test_expected_move` baseline failures.

**Step 7: Commit**

```bash
git add services/options_svc/scheduler.py services/options_svc/handlers.py \
        services/options_svc/tests/test_scheduler.py services/options_svc/tests/test_handlers.py \
        services/options_svc/push_notify.py
git commit -m "feat(briefing): close slot 14:58 -> 15:15 CT, routed to the EOD retrospective"
```

---

## Task 8: Live end-to-end verification

**No new files — this is the gate that matters.** Every prior task used fakes; three real
bugs in this codebase's history (the `days=6` Schwab 400, the string `total_score`, the
`CacheEnvelope` push that never fired) passed every unit test and were caught only here.

**Step 1: Restart the service**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -c "import sys,pathlib; sys.path.insert(0,str(pathlib.Path('.').resolve())); print('imports clean')"
```
Then restart `options_svc` (Status page Restart button, or relaunch `services/options_svc/app.py`).

**Step 2: Run the EOD briefing for real**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -c "
import sys,pathlib; sys.path.insert(0,str(pathlib.Path('.').resolve()))
from services.options_svc import compute
res = compute.eod_briefing(label='Manual probe')
a = res.get('analysis') or {}
print('analysis?', bool(a))
print('regime  :', a.get('regime'))
print('headline:', a.get('headline'))
print('drivers :', a.get('macro_drivers'))
print('movers  :', [m.get('symbol') for m in (a.get('movers') or [])])
print('next    :', a.get('next_session'))
open(r'C:\\Users\\john_\\AppData\\Local\\Temp\\claude\\eod_probe.html','w',encoding='utf-8').write(res['html'])
"
```
**Verify, explicitly:**
- `analysis?` is `True` (a `False` means it degraded — find out which surface and fix).
- `macro_drivers` is **non-empty** — an empty list means the web-search phase silently
  degraded (the exact failure mode Task 2 Step 5 exists to catch).
- `movers` names real symbols.
- `next_session` talks about tomorrow, and **the headline/narrative are retrospective** — if
  any prose still says "buy dips into the close", the system prompt needs tightening. That is
  the whole point of this feature.
- Open the written HTML in a browser and eyeball the layout.

**Step 3: Verify an intraday briefing still works and is enriched**

```bash
cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -c "
import sys,pathlib; sys.path.insert(0,str(pathlib.Path('.').resolve()))
from services.options_svc import compute
a = (compute.gamma_analyze(label='Manual probe') or {}).get('analysis') or {}
print('drivers:', a.get('macro_drivers')); print('movers :', a.get('movers'))
print('what_if intact:', bool((a.get('indices') or [{}])[0].get('what_if')))
"
```
Expected: drivers + movers populated **and** the existing forward playbook (`what_if`,
`close_outlook`) intact — this is an enrichment, not a replacement.

**Step 4: Verify the push** — confirm the EOD briefing PNG actually arrives in Telegram +
Discord (it rides the existing `send_gamma_briefing`; a render failure falls back to text).

**Step 5: Commit any fixes the live run exposed**, then update the docs:
- `CLAUDE.md`: a dated "Last updated" entry — the close slot is now a **15:15 CT EOD
  retrospective** (not a pre-close playbook), all four briefings carry macro drivers +
  notable movers, the news phase is a second Claude call with the web-search tool that
  degrades to app-data-only, and the cost is now ~8 Claude calls + 4 web searches/day for
  briefings. Note **restart `options_svc`**.
- Route table: the Gamma page's "Auto briefings → Close" now opens the EOD recap.

```bash
git add CLAUDE.md
git commit -m "docs: EOD retrospective briefing + macro drivers/movers across briefings"
```

---

## Definition of done

- [ ] `close` fires at **15:15 CT** and produces a **retrospective** (no intraday advice for
      a closed session).
- [ ] All four briefings show **macro drivers** + **notable individual stock moves**.
- [ ] The EOD briefing has a **"prepare for next session"** block (levels, EM, catalysts,
      posture).
- [ ] Web-search failure / no key / no chains / no movers each degrade gracefully — the
      briefing always renders.
- [ ] The intraday briefings' existing playbook (`what_if`, `close_outlook`) is unchanged.
- [ ] `pytest services/options_svc -q` green except the 2 documented baseline failures.
- [ ] Live-verified end-to-end: real Claude call, real headlines, real movers, PNG pushed.
- [ ] `CLAUDE.md` updated; `options_svc` restarted.
