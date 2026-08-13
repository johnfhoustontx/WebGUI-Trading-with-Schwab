# EquityDeepDive → Trade Analyzer button — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add **Deep Dive** + **AI Query** buttons to `/trade` that, for the current symbol, run the migrated EquityDeepDive quant engine and open a cached HTML report / a copyable chat-prompt — with the engine living in a Tier-2 subpackage of `trade_svc` and **no Anthropic API calls**.

**Architecture:** Migrate `equity_deep_dive.py` / `iv_history.py` / `make_chat_prompt.py` (+ `build_quant_digest` extracted from `ai_analyst.py`) into `services/trade_svc/deepdive/`, lightly adapted (relative imports, repo_paths for proxy/DB, CLI stripped, `render_html` returns a string). The service adds `run_deep_dive` / `build_deep_dive_query` + `deepdive`/`deepdive_query` commands writing `cache:trade:deepdive` / `cache:trade:deepdive_query`. The webgui adds two buttons (enqueue + version-poll → open a new tab) and two serve routes (raw HTML / copyable prompt page), mirroring the proven `/options/analyze` pattern. **`ai_analyst.py` is not migrated** (no API).

**Tech Stack:** Python, pandas/numpy/requests (already present — no new deps), NiceGUI, FastAPI (serve routes), Redis via `shared.bus`, SQLite (iv_history), pytest.

**Branch:** `Using_Highcharts` (work directly on it). Design: [`2026-08-04-equity-deep-dive-trade-button-design.md`](2026-08-04-equity-deep-dive-trade-button-design.md).

**Source files (external, copied in):** `D:\AI_Based_Analysis\EquityDeepDive\{equity_deep_dive,iv_history,make_chat_prompt,ai_analyst}.py`, `chat_query_template.md`.

---

### Task 1: Migrate the `deepdive` subpackage

**Files:**
- Create: `services/trade_svc/deepdive/__init__.py`
- Create: `services/trade_svc/deepdive/iv_history.py` (copy, verbatim)
- Create: `services/trade_svc/deepdive/chat_query_template.md` (copy, verbatim)
- Create: `services/trade_svc/deepdive/digest.py` (extract from `ai_analyst.py`)
- Create: `services/trade_svc/deepdive/engine.py` (copy `equity_deep_dive.py` + edits)
- Create: `services/trade_svc/deepdive/chat_prompt.py` (copy `make_chat_prompt.py` + edits)
- Modify: `repo_paths.py` (add `IV_HISTORY_DB`)
- Modify: `.gitignore` (ensure `services/trade_svc/data/` ignored)

**Step 1: Copy the source files into the new subpackage**

```bash
cd "D:/WebGUI Trading with Schwab"
mkdir -p services/trade_svc/deepdive services/trade_svc/data
: > services/trade_svc/deepdive/__init__.py
cp "D:/AI_Based_Analysis/EquityDeepDive/iv_history.py"          services/trade_svc/deepdive/iv_history.py
cp "D:/AI_Based_Analysis/EquityDeepDive/chat_query_template.md" services/trade_svc/deepdive/chat_query_template.md
cp "D:/AI_Based_Analysis/EquityDeepDive/equity_deep_dive.py"    services/trade_svc/deepdive/engine.py
cp "D:/AI_Based_Analysis/EquityDeepDive/make_chat_prompt.py"    services/trade_svc/deepdive/chat_prompt.py
```

**Step 2: Create `digest.py`** (the pure digest formatter, no API)

Copy `_fmt` (ai_analyst.py:328-334) and `build_quant_digest` (ai_analyst.py:337-470) into `services/trade_svc/deepdive/digest.py`, prefixed with:

```python
"""Pure quant-digest formatter (extracted from EquityDeepDive's ai_analyst.py).

Formats an ``equity_deep_dive`` result dict into a dense text block for the chat
prompt. No API calls — this is the *formatter* only; the Anthropic client was left
behind by design (the 3-tier button generates a query, it does not call the API).
"""
import datetime as dt

# <paste _fmt and build_quant_digest here, verbatim>
```

**Step 3: Adapt `engine.py`** (5 edits; keep all compute + `render_html`)

1. `import iv_history as ivh` → `from . import iv_history as ivh`
2. Add near the top imports: `from repo_paths import PROXY_URL`
3. Replace `PROXY_BASE = 'http://127.0.0.1:8100'` → `PROXY_BASE = PROXY_URL`
4. Delete the module-level `logging.basicConfig(...)` call (keep `logger = logging.getLogger(__name__)`).
5. Replace `TOKEN_PATH = Path(r'D:\AI_Based_Analysis\SchwabProxy\tokens.json')` → `TOKEN_PATH = None  # direct mode unused in-service (always proxy)`
6. Delete the trailing `if __name__ == '__main__':\n    main()` block.

(The CLI functions `main`/`parse_args`/`write_outputs`/etc. stay as inert dead code — they reference nothing repo-specific and keep ruff's unused-import check quiet. Not worth deleting.)

**Step 4: Adapt `chat_prompt.py`** (3 edits)

1. Delete the module-level `logging.basicConfig(...)` call (keep `logger = logging.getLogger(__name__)`).
2. Replace the body of `get_digest(data)` with the local digest module:
   ```python
   def get_digest(data):
       """Build the quantitative digest via the local (pure, no-API) formatter."""
       from . import digest
       return digest.build_quant_digest(data)
   ```
3. Delete `parse_args`, `main`, and the trailing `if __name__ == '__main__':` block (keep `build_prompt`, `get_digest`, `find_template`, `latest_dump`). `find_template` already locates `chat_query_template.md` beside `__file__` — no change needed.

**Step 5: Add `IV_HISTORY_DB` to `repo_paths.py`**

After the other `*_DB` constants (near `repo_paths.py:63`), add:
```python
# EquityDeepDive IV/RV history (migrated into trade_svc; on-demand, gitignored).
IV_HISTORY_DB = REPO_ROOT / "services" / "trade_svc" / "data" / "iv_history.db"
```

**Step 6: Ensure the data dir is gitignored**

Check `.gitignore` for a `data/` rule:
Run: `grep -nE "(^|/)data/?$|trade_svc/data" .gitignore || echo "NOT IGNORED"`
If it prints `NOT IGNORED`, append `services/trade_svc/data/` to `.gitignore`. (A generic `data/` line already covers it in this repo — verify.)

**Step 7: Verify the package imports cleanly (the load-bearing smoke test)**

Run:
```bash
.venv/Scripts/python -c "from services.trade_svc.deepdive import engine, iv_history, chat_prompt, digest; print('OK', engine.PROXY_BASE, hasattr(engine,'analyze_symbol'), hasattr(engine,'render_html'), hasattr(chat_prompt,'build_prompt'), hasattr(digest,'build_quant_digest'))"
```
Expected: `OK http://127.0.0.1:8100 True True True True` — no CLI output, no `basicConfig` side effects, relative imports resolve.

**Step 8: Commit**

```bash
git add services/trade_svc/deepdive repo_paths.py .gitignore
git commit -m "feat(trade): migrate EquityDeepDive engine into trade_svc/deepdive"
```

---

### Task 2: Service compute wrappers

**Files:**
- Modify: `services/trade_svc/compute.py` (add three functions at the end)
- Test: `services/trade_svc/tests/test_deepdive.py`

**Step 1: Write the failing test**

Create `services/trade_svc/tests/test_deepdive.py`:
```python
"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_deepdive.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import types
from services.trade_svc import compute


_FAKE_RESULT = {
    "symbol": "OKLO", "quote": {"lastPrice": 12.3},
    "technicals": {"last_close": 12.3, "rvol_20d": 55.0},
    "fundamentals": {"pe_ratio": None},
    "options": {"available": False}, "ranks": {}, "takeaways": ["t1"],
}


def test_run_deep_dive_returns_html(monkeypatch):
    # Stub the engine so no proxy/DB is touched: analyze_symbol -> a canned result,
    # render_html -> a sentinel HTML string.
    from services.trade_svc.deepdive import engine
    monkeypatch.setattr(engine, "SchwabClient", lambda *a, **k: object())
    monkeypatch.setattr(engine, "analyze_symbol", lambda *a, **k: dict(_FAKE_RESULT))
    monkeypatch.setattr(engine, "render_html", lambda *a, **k: "<html>DEEP DIVE</html>")
    monkeypatch.setattr(compute, "_open_iv_conn", lambda: None)  # skip real SQLite

    res = compute.run_deep_dive("oklo")
    assert res["symbol"] == "OKLO"
    assert "DEEP DIVE" in res["html"]
    assert res["ts"]


def test_run_deep_dive_bad_symbol_returns_error_html():
    res = compute.run_deep_dive("")
    assert res["symbol"] == "?"
    assert "html" in res and res["html"]  # a friendly error page, never None/raise


def test_build_deep_dive_query_injects_digest(monkeypatch):
    from services.trade_svc.deepdive import engine
    monkeypatch.setattr(engine, "SchwabClient", lambda *a, **k: object())
    monkeypatch.setattr(engine, "analyze_symbol", lambda *a, **k: dict(_FAKE_RESULT))
    monkeypatch.setattr(compute, "_open_iv_conn", lambda: None)

    res = compute.build_deep_dive_query("OKLO")
    md = res["markdown"]
    assert res["symbol"] == "OKLO"
    assert "OKLO" in md                 # {{SYMBOL}} substituted
    assert "{{QUANT_DATA}}" not in md    # placeholder filled
    assert "<!--" not in md              # HOW-TO comment stripped
```

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\trade_svc\tests\test_deepdive.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'run_deep_dive'`).

**Step 3: Implement** — append to `services/trade_svc/compute.py`:

```python
# ── EquityDeepDive (migrated) — on-demand quant deep dive + chat-prompt query ──
_DEEPDIVE_ARGS = dict(years=1, no_options=False, strikes=40,
                      from_date=None, to_date=None, lookback=252)


def _open_iv_conn():
    """Open (creating if needed) the IV/RV history SQLite store. Isolated so tests
    can stub it away."""
    from repo_paths import IV_HISTORY_DB
    from services.trade_svc.deepdive import iv_history as ivh
    IV_HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    return ivh.init_db(IV_HISTORY_DB)


def _deep_dive_result(symbol):
    """Run the migrated EquityDeepDive engine for one symbol → its result dict (or
    None). Records an IV/RV snapshot as a side effect. Defensive: never raises."""
    from types import SimpleNamespace
    from services.trade_svc.deepdive import engine, iv_history as ivh
    symbol = engine.normalize_symbol((symbol or "").strip().upper())
    if not symbol:
        return None, "?"
    args = SimpleNamespace(**_DEEPDIVE_ARGS)
    conn = None
    try:
        client = engine.SchwabClient()  # proxy mode (repo_paths.PROXY_URL)
        conn = _open_iv_conn()
        result = engine.analyze_symbol(client, symbol, args, conn)
    except Exception:
        result = None
    finally:
        if conn is not None:
            try:
                ivh.close_db(conn)
            except Exception:
                pass
    return result, symbol


def run_deep_dive(symbol):
    """→ {'symbol','html','ts'}. Renders the deep-dive HTML report (or a friendly
    error page). Never raises."""
    from services.trade_svc.deepdive import engine
    result, sym = _deep_dive_result(symbol)
    if not result:
        html = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>Deep Dive — {sym}</title></head>"
                f"<body style='font-family:system-ui;background:#0c0f15;color:#e9edf3;padding:40px'>"
                f"<h3>Could not run a deep dive for {sym}</h3>"
                f"<p>Check the symbol, and that the Schwab proxy (:8100) is up and its "
                f"token is valid (visit <code>http://127.0.0.1:8100/auth</code>).</p>"
                f"</body></html>")
        return {"symbol": sym, "html": html, "ts": _now_iso()}
    try:
        html = engine.render_html(
            result["symbol"], result["quote"], result["technicals"],
            result["fundamentals"], result["options"], result["takeaways"],
            result["ranks"])
    except Exception as exc:
        html = f"<html><body><h3>Deep Dive render failed for {sym}: {exc}</h3></body></html>"
    return {"symbol": sym, "html": html, "ts": _now_iso()}


def build_deep_dive_query(symbol):
    """→ {'symbol','markdown','ts'}. Builds the chat prompt (digest injected,
    HOW-TO stripped) for the user to paste into a chat. NO API call. Never raises."""
    from services.trade_svc.deepdive import chat_prompt
    result, sym = _deep_dive_result(symbol)
    if not result:
        return {"symbol": sym, "ts": _now_iso(),
                "markdown": f"Could not build a query for {sym}. Is the proxy up?"}
    try:
        template_text = chat_prompt.find_template().read_text(encoding="utf-8")
        md = chat_prompt.build_prompt(result, template_text)
    except Exception as exc:
        md = f"Query build failed for {sym}: {exc}"
    return {"symbol": sym, "markdown": md, "ts": _now_iso()}
```

> Verify `compute._now_iso()` exists (the module already uses it — e.g. in `analyze`). If it is named differently, reuse that helper.

**Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest services\trade_svc\tests\test_deepdive.py -q`
Expected: PASS (3 tests).

**Step 5: Commit**

```bash
git add services/trade_svc/compute.py services/trade_svc/tests/test_deepdive.py
git commit -m "feat(trade): run_deep_dive + build_deep_dive_query service wrappers"
```

---

### Task 3: Command handlers

**Files:**
- Modify: `services/trade_svc/handlers.py`
- Test: `services/trade_svc/tests/test_deepdive.py` (append)

**Step 1: Write the failing test** — append:
```python
class _FakeBus:
    def __init__(self):
        self.sets = {}
        self.published = []
    def cache_set(self, key, payload):
        self.sets[key] = payload
        return 1
    def publish(self, event, msg):
        self.published.append((event, msg))


def test_handle_command_deepdive(monkeypatch):
    from services.trade_svc import handlers
    monkeypatch.setattr(compute, "run_deep_dive",
                        lambda s: {"symbol": s.upper(), "html": "<h1>H</h1>", "ts": "t"})
    bus = _FakeBus()
    handlers.handle_command(bus, types.SimpleNamespace(type="deepdive", args={"symbol": "oklo"}))
    assert "OKLO" in bus.sets["cache:trade:deepdive"]["symbol"]
    assert bus.published and bus.published[0][0] == "events:trade:deepdive"


def test_handle_command_deepdive_query(monkeypatch):
    from services.trade_svc import handlers
    monkeypatch.setattr(compute, "build_deep_dive_query",
                        lambda s: {"symbol": s.upper(), "markdown": "PROMPT", "ts": "t"})
    bus = _FakeBus()
    handlers.handle_command(bus, types.SimpleNamespace(type="deepdive_query", args={"symbol": "oklo"}))
    assert bus.sets["cache:trade:deepdive_query"]["markdown"] == "PROMPT"
```

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\trade_svc\tests\test_deepdive.py -k deepdive -q`
Expected: FAIL (`KeyError: 'cache:trade:deepdive'` — handler not wired).

**Step 3: Implement** — in `services/trade_svc/handlers.py`:

Add constants beside the existing ones:
```python
CACHE_DEEPDIVE = "cache:trade:deepdive"
EVENT_DEEPDIVE = "events:trade:deepdive"
CACHE_DEEPDIVE_QUERY = "cache:trade:deepdive_query"
EVENT_DEEPDIVE_QUERY = "events:trade:deepdive_query"
```
Add handlers:
```python
def deepdive(bus, args) -> None:
    """Run the EquityDeepDive quant report for the symbol, cache HTML + publish."""
    res = compute.run_deep_dive((args or {}).get("symbol", ""))
    version = bus.cache_set(CACHE_DEEPDIVE, res)
    bus.publish(EVENT_DEEPDIVE, {"version": version})


def deepdive_query(bus, args) -> None:
    """Build the chat-prompt query for the symbol, cache markdown + publish."""
    res = compute.build_deep_dive_query((args or {}).get("symbol", ""))
    version = bus.cache_set(CACHE_DEEPDIVE_QUERY, res)
    bus.publish(EVENT_DEEPDIVE_QUERY, {"version": version})
```
Extend `handle_command`:
```python
def handle_command(bus, command) -> None:
    if command.type == "analyze":
        analyze(bus, command.args)
    elif command.type == "deepdive":
        deepdive(bus, command.args)
    elif command.type == "deepdive_query":
        deepdive_query(bus, command.args)
```

**Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest services\trade_svc\tests\test_deepdive.py -q`
Expected: PASS (5 tests).

**Step 5: Commit**

```bash
git add services/trade_svc/handlers.py services/trade_svc/tests/test_deepdive.py
git commit -m "feat(trade): deepdive + deepdive_query command handlers"
```

---

### Task 4: Serve routes (webgui)

**Files:**
- Modify: `webgui/main.py` (two routes + two pure helpers)
- Test: `webgui/tests/test_deepdive_routes.py`

**Step 1: Write the failing test**

Create `webgui/tests/test_deepdive_routes.py`:
```python
from webgui import main


def test_deepdive_html_extracts_and_falls_back():
    assert "REPORT" in main.deepdive_html({"html": "<h1>REPORT</h1>"})
    empty = main.deepdive_html(None)
    assert "Deep Dive" in empty and "<html" in empty.lower()  # placeholder page


def test_deepdive_query_html_wraps_markdown():
    page = main.deepdive_query_html({"markdown": "PASTE ME", "symbol": "OKLO"})
    assert "PASTE ME" in page          # the prompt is embedded
    assert "clipboard" in page.lower()  # a Copy button is present
    fallback = main.deepdive_query_html(None)
    assert "<html" in fallback.lower()
```

**Step 2: Run to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_deepdive_routes.py -q`
Expected: FAIL (`AttributeError: module 'webgui.main' has no attribute 'deepdive_html'`).

**Step 3: Implement** — in `webgui/main.py`, beside the `/options/analyze` block (~line 160):

```python
_DEEPDIVE_EMPTY = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>Deep Dive</title></head>"
    "<body style='font-family:system-ui,sans-serif;background:#0c0f15;color:#e9edf3;padding:40px'>"
    "<h3>No Deep Dive generated yet</h3><p>Open the Trade Analyzer, enter a symbol, and click "
    "<b>Deep Dive</b>.</p></body></html>")


def deepdive_html(payload):
    """Standalone deep-dive report HTML from the cached payload (or a placeholder)."""
    html = (payload or {}).get("html")
    return html if isinstance(html, str) and html.strip() else _DEEPDIVE_EMPTY


def deepdive_query_html(payload):
    """Wrap the cached chat-prompt markdown in a dark, copyable page (textarea +
    Copy button) so the user can paste it straight into a chat."""
    import html as _h
    md = (payload or {}).get("markdown")
    sym = (payload or {}).get("symbol", "")
    if not (isinstance(md, str) and md.strip()):
        return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>AI Query</title></head>"
                "<body style='font-family:system-ui;background:#0c0f15;color:#e9edf3;padding:40px'>"
                "<h3>No query generated yet</h3><p>Click <b>AI Query</b> on the Trade Analyzer.</p>"
                "</body></html>")
    esc = _h.escape(md)
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>AI Query — {_h.escape(sym)}</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#0c0f15;color:#e9edf3;margin:0;padding:24px}"
        "h3{margin:0 0 12px}button{background:#2563eb;color:#fff;border:0;border-radius:8px;"
        "padding:10px 16px;font-weight:600;cursor:pointer}button:hover{background:#1d4fd1}"
        "textarea{width:100%;height:75vh;margin-top:12px;background:#101a30;color:#e7edf8;"
        "border:1px solid #243353;border-radius:8px;padding:12px;font-family:ui-monospace,monospace;"
        "font-size:12px;box-sizing:border-box}</style></head><body>"
        f"<h3>AI Query — {_h.escape(sym)} "
        "<button onclick=\"navigator.clipboard.writeText(document.getElementById('q').value)."
        "then(()=>{this.textContent='Copied!';setTimeout(()=>this.textContent='Copy',1200)})\">Copy</button></h3>"
        f"<textarea id='q' readonly>{esc}</textarea></body></html>")


@app.get("/trade/deepdive")
def _serve_deepdive():
    """Serve the latest Deep Dive report as a raw standalone page (its own <style>
    applies). Opened in a new browser tab from the Trade page."""
    import bus_client
    return HTMLResponse(deepdive_html(bus_client.read("trade:deepdive")))


@app.get("/trade/deepdive-query")
def _serve_deepdive_query():
    """Serve the latest AI Query as a copyable page. Opened in a new tab."""
    import bus_client
    return HTMLResponse(deepdive_query_html(bus_client.read("trade:deepdive_query")))
```

**Step 4: Run to verify it passes**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_deepdive_routes.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_deepdive_routes.py
git commit -m "feat(trade): serve routes for the Deep Dive report + AI Query"
```

---

### Task 5: Trade-page buttons + watchers

**Files:**
- Modify: `webgui/pages/trade.py`
- Test: `webgui/tests/test_trade.py` (append a pure-helper test)

**Step 1: Write the failing test** — append to `webgui/tests/test_trade.py`:
```python
def test_should_open_tab():
    # open only when a request is pending AND the cache version advanced past the
    # baseline captured at click time (so a page-load with a stale result never opens).
    assert trade.should_open_tab(pending=True, version=5, baseline=4) is True
    assert trade.should_open_tab(pending=True, version=4, baseline=4) is False
    assert trade.should_open_tab(pending=False, version=9, baseline=4) is False
```

**Step 2: Run to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py::test_should_open_tab -q`
Expected: FAIL (`AttributeError: ... 'should_open_tab'`).

**Step 3: Implement**

Add the pure helper near the other helpers in `webgui/pages/trade.py`:
```python
def should_open_tab(pending, version, baseline):
    """Open the report/query tab only when a click is pending AND the cache version
    advanced past the baseline captured at click time."""
    return bool(pending) and version is not None and version != baseline
```

In `render()`, extend `state` with the watch keys (beside the existing keys):
```python
    state.update({"dd_ver": None, "dd_pending": False,
                  "q_ver": None, "q_pending": False})
    state["dd_ver"] = bus_client.read_version("trade:deepdive")
    state["q_ver"] = bus_client.read_version("trade:deepdive_query")
```

Add the two buttons in the controls row (after `analyze_btn`), themed like the shared secondary button:
```python
            deepdive_btn = ui.button("Deep Dive", icon="query_stats", color=None) \
                .props("no-caps").classes("cv2-btn")
            query_btn = ui.button("AI Query", icon="content_copy", color=None) \
                .props("no-caps").classes("cv2-btn")
```

Add the request + watcher wiring (near the existing `_request_analyze` / `_poll`):
```python
    @guard
    def _request_deepdive():
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            return
        state["dd_ver"] = bus_client.read_version("trade:deepdive")  # baseline
        state["dd_pending"] = True
        bus_client.request("trade", {"type": "deepdive", "args": {"symbol": sym}})
        status.text = f"Running Deep Dive for {sym}…"

    @guard
    def _request_query():
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            return
        state["q_ver"] = bus_client.read_version("trade:deepdive_query")
        state["q_pending"] = True
        bus_client.request("trade", {"type": "deepdive_query", "args": {"symbol": sym}})
        status.text = f"Building AI Query for {sym}…"

    deepdive_btn.on_click(_request_deepdive)
    query_btn.on_click(_request_query)

    @guard
    def _watch_deepdive():
        v = bus_client.read_version("trade:deepdive")
        if should_open_tab(state["dd_pending"], v, state["dd_ver"]):
            state["dd_pending"] = False
            state["dd_ver"] = v
            ui.navigate.to(f"/trade/deepdive?v={v}", new_tab=True)
        qv = bus_client.read_version("trade:deepdive_query")
        if should_open_tab(state["q_pending"], qv, state["q_ver"]):
            state["q_pending"] = False
            state["q_ver"] = qv
            ui.navigate.to(f"/trade/deepdive-query?v={qv}", new_tab=True)

    ui.timer(2.0, _watch_deepdive)
```

> Browsers block programmatic `window.open`/new-tab navigation that isn't tied to a
> user gesture; because the tab opens ~seconds later from a timer, the pop-up may be
> blocked on the first run. This is the SAME behavior the Gamma page's `_watch_analyze`
> already ships with (documented), so we match it. (If it proves annoying in live
> verification, a fallback is a "report ready — open" link in `status`; note but don't
> pre-build it — YAGNI.)

**Step 4: Run to verify it passes**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py -q`
Expected: PASS (all, incl. `test_should_open_tab` + the render smoke test).

**Step 5: Commit**

```bash
git add webgui/pages/trade.py webgui/tests/test_trade.py
git commit -m "feat(trade): Deep Dive + AI Query buttons open report/query tabs"
```

---

### Task 6: Full sweep + live verification + docs

**Files:** none (verification) + `CLAUDE.md`

**Step 1: Run both suites**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`  → all green.
Run: `.venv\Scripts\python -m pytest services\trade_svc\tests -q`  → all green.

**Step 2: Ruff check the new code**

Run: `.venv\Scripts\python -m ruff check services/trade_svc/deepdive services/trade_svc/compute.py services/trade_svc/handlers.py webgui/main.py webgui/pages/trade.py`
Expected: clean (fix any unused-import/style flags the migration introduced).

**Step 3: Restart `trade_svc` + the webgui**

```bash
tools\restart_one.bat 8213 8100 services\trade_svc\app.py
```
(and restart the webgui the same way for the new routes/page: `tools\restart_one.bat 8500 8100 webgui\main.py`).

**Step 4: Live-verify end-to-end (Redis-driven)**

```bash
.venv/Scripts/python -c "
import time
from shared.bus import Bus
b = Bus()
for t in ['deepdive', 'deepdive_query']:
    v0 = b.cache_version('cache:trade:'+t.replace('deepdive_query','deepdive_query'))
    b.enqueue_command('cmd:trade', {'type': t, 'args': {'symbol':'OKLO'}})
    time.sleep(20)
    env = b.cache_get('cache:trade:'+('deepdive_query' if t=='deepdive_query' else 'deepdive'))
    p = env.payload if env else {}
    key = 'markdown' if t=='deepdive_query' else 'html'
    print(t, 'symbol=', p.get('symbol'), 'len=', len(p.get(key,'') or ''))
"
```
Expected: both print a non-trivial length for a real symbol; a fresh `services/trade_svc/data/iv_history.db` now exists with an OKLO snapshot (`.venv\Scripts\python -c "import sqlite3;print(sqlite3.connect(r'services/trade_svc/data/iv_history.db').execute('select count(*) from iv_snapshots').fetchone())"`).

**Step 5: Browser check** — open `:8500/trade`, enter a symbol, click **Deep Dive** (report tab opens) and **AI Query** (copyable prompt tab opens, Copy works). Screenshot both buttons + the report.

**Step 6: Update CLAUDE.md + commit**

Prepend a "Last updated" entry (EquityDeepDive migrated into `trade_svc/deepdive`; Deep Dive + AI Query buttons; no API — the AI note is a generated query; IV history on-demand) and update the `/trade` route row + the `services` folder note. Commit:
```bash
git add CLAUDE.md
git commit -m "docs: note EquityDeepDive Deep Dive/AI Query buttons on /trade"
```

---

## Notes for the executor
- **No new pip deps** — the engine uses pandas/numpy/requests (present). Confirm no stray `import anthropic` slipped in (it must not — `ai_analyst.py` is NOT migrated).
- **3-tier rule preserved:** the webgui only enqueues commands + serves cached output; ALL Schwab/compute lives in `trade_svc`.
- **Service-test isolation:** run `services\trade_svc\tests` ALONE, never `pytest services`.
- The engine's compute is reused as-is (low regression risk); the seam is the small wrapper + handlers + routes + buttons. Verify the engine's real output live (Step 4) — its analytics aren't unit-retrofitted.
- If `find_template()` can't locate `chat_query_template.md`, confirm Step 1 copied it INTO `services/trade_svc/deepdive/` (it must sit beside `chat_prompt.py`).
