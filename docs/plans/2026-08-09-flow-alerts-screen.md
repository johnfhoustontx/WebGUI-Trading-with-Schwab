# Flow Alerts Screen Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `/options/flow` — a screen listing today's options-flow alerts newest-first, with fired time, live age, filters, and a click-through to Dealer Positioning.

**Architecture:** A pure Tier-1 NiceGUI page that reads the existing `cache:options:flow_alerts` key and nothing else — no new service, command, or cache key. Pure builders are module-level and unit-tested; `render()` is thin wiring that version-polls the bus. Two one-line changes land in `options_svc` (raise the day-list cap, stamp a timestamp on unusual-activity alerts, which currently have none).

**Tech Stack:** Python 3.11, NiceGUI (Quasar tables, Tailwind-first styling), Redis/Memurai via `shared.bus`, pytest.

**Design doc:** [`docs/plans/2026-08-09-flow-alerts-screen-design.md`](2026-08-09-flow-alerts-screen-design.md)

---

## Orientation — read before Task 1

You are in a **git worktree** at `D:\WebGUI Trading with Schwab\.claude\worktrees\stoic-saha-cb9613` on branch `claude/flow-alerts-screen-60a844`. The worktree has **no `.venv` of its own** — the interpreter is at the main repo root:

```
D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe
```

Every command below uses that absolute path. Two test suites matter:

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui -q
```

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q
```

⚠ **Always `cd` to the worktree ROOT, never into a subdirectory.** This project's hooks resolve their own path relative to the shell's persistent working directory, and a `cd webgui` wedges every shell + file-writing tool for the rest of the session. Run the webgui suite as `pytest webgui` from the root, not `cd webgui && pytest`.

**Never run `pytest services`** across all services — that puts several hyphenated app dirs on `sys.path` at once and re-triggers the documented `config`/`scoring`/`notifier` module-name collisions.

**Known baseline failures — do NOT try to fix them, and do not read them as your regression:** `options_svc` has 2 date-relative `test_expected_move` failures. Note the failure count *before* you start so you can tell your own breakage apart from theirs.

**Do not launch the app from this worktree.** There is no `config/env.local.toml` here, so the checkout resolves to **prod** — a prod launcher started from here would bind prod's `:8100`. Verification in this plan is test-based; live browser verification happens later in the dev checkout, per THE DEVELOPMENT RULE in CLAUDE.md.

### What a flow alert looks like

`cache:options:flow_alerts` holds `{"date": "2026-08-09", "alerts": [...]}` — append-ordered (oldest first), day-scoped. Three alert shapes, all carrying `id`, `type`, `side`, `symbol`, `text`:

```python
# crossover — put/call premium lead flipped sign
{"type": "crossover", "side": "calls_over",  # or "puts_over"
 "symbol": "SPY", "ts": 1754750000,
 "call_prem": 1200000.0, "put_prem": 400000.0,
 "id": "SPY|crossover|calls_over|1754750000", "text": "SPY — call premium overtook puts: …"}

# uoa — one contract with unusual volume vs open interest. NOTE: no "ts" until Task 1.
{"type": "uoa", "side": "call",  # or "put"
 "symbol": "SPY", "strike": 737.0, "expiry": "2026-08-09", "dte": 0,
 "cost": 1.72, "volume": 12400, "oi": 1100, "vol_oi": 11.27, "premium": 2132800.0,
 "id": "SPY|uoa|call|737|2026-08-09", "text": "SPY 0DTE 737C — UNUSUAL: …"}

# gamma_flip — spot crossed the dealer gamma flip level
{"type": "gamma_flip", "side": "to_negative",  # or "to_positive"
 "symbol": "$SPX", "spot": 6412.0, "flip": 6400.0, "ts": 1754750400,
 "id": "$SPX|gamma_flip|to_negative|1754750400", "text": "$SPX — gamma flipped NEGATIVE: …"}
```

### House rules that this plan is built around

- **Tailwind-first is mandatory.** No `.style(...)`, no `style=` attribute strings, no Vue `:style=` slot bindings. A data-driven color maps from its **finite set** to a **static** Tailwind class via a small pure lookup, bound with `:class`. A guard test enforces this.
- **Tier-1 pages import only** `nicegui`, `bus_client`, and `pages.*` helpers — never an engine or service module.
- **Pure builders are module-level and total** — they must survive `None`, a malformed payload, and missing keys without raising, because `render()` does no validation.
- **Wrap every timer/event callback** in `pages.ui_guard.guard` / `guard_async`, so a closed browser tab doesn't spray tracebacks.

---

## Task 1: options_svc — timestamp UOA alerts and raise the day cap

Unusual-activity alerts carry no `ts`, so a third of the alert types would render a blank Time cell. And the published list is capped at 50, which silently drops the morning's alerts on a busy day.

**Files:**
- Modify: `services/options_svc/handlers.py:251` (the `_FLOW_ALERTS_MAX` constant)
- Modify: `services/options_svc/handlers.py:1034-1042` (the UOA drain loop)
- Test: `services/options_svc/tests/test_handlers.py` (append)

**Step 1: Read the existing test file's flow-alert section**

Run: `grep -n "run_flow_alerts\|uoa\|class _Fake\|def _bus" services/options_svc/tests/test_handlers.py | head -40`

Match the fixtures and fake-bus idiom already there — do NOT invent a new one. In particular note how the tests fake `compute.take_uoa_stash` and construct the bus.

**Step 2: Write the failing tests**

Append to `services/options_svc/tests/test_handlers.py`, adapting the fake-bus setup to match that file's existing helpers:

```python
def test_flow_alerts_cap_holds_a_full_day():
    """The published list was capped at 50, which drops the morning's alerts before
    anyone can look at them. A full session across ~45 symbols needs headroom."""
    from services.options_svc import handlers
    assert handlers._FLOW_ALERTS_MAX >= 300


def test_uoa_alert_carries_a_timestamp(monkeypatch):
    """Crossover and gamma_flip alerts carry ts; UOA did not, so a chronological
    view had nothing to sort or display for a third of its rows."""
    from services.options_svc import handlers

    monkeypatch.setattr(handlers.compute, "take_uoa_stash", lambda: {"SPY": [
        {"type": "uoa", "side": "call", "symbol": "SPY", "strike": 737.0,
         "expiry": "2026-08-09", "dte": 0, "cost": 1.72, "volume": 12400,
         "oi": 1100, "vol_oi": 11.27, "premium": 2132800.0}]})
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 1754750000)

    bus = _FakeBus()          # use this file's existing fake-bus helper
    handlers.run_flow_alerts(bus)

    alerts = bus.cache_get(handlers.CACHE_FLOW_ALERTS).payload["alerts"]
    uoa = [a for a in alerts if a["type"] == "uoa"]
    assert uoa and uoa[0]["ts"] == 1754750000
```

**Step 3: Run the tests to verify they fail**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_handlers.py -q -k "cap_holds or carries_a_timestamp"`

Expected: 2 failures — `assert 50 >= 300`, and a `KeyError: 'ts'`.

**Step 4: Make the changes**

In `services/options_svc/handlers.py`, change the constant:

```python
# Cap the day's published list. 50 dropped the morning's alerts on a busy day;
# ~45 symbols with a 30-min crossover cooldown, a $5M UOA floor and 4 gamma-flip
# names fit comfortably in 300.
_FLOW_ALERTS_MAX = 300
```

And in the UOA drain loop (~line 1039), stamp the tick's timestamp:

```python
                cooldowns[cid] = now_ts
                a = dict(c)
                a["id"] = cid
                # detect_uoa emits no ts (crossover/gamma_flip do) — stamp the
                # detecting tick so every alert can be placed on a timeline.
                a["ts"] = now_ts
                a["text"] = flow_alerts.alert_text(a)
```

**Step 5: Run the tests to verify they pass**

Run the same command as Step 3. Expected: 2 passed.

**Step 6: Run the whole options_svc suite**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q`

Expected: only the 2 pre-existing date-relative `test_expected_move` failures. If anything else fails, you broke it — fix it before committing.

**Step 7: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "fix(flow): stamp UOA alerts with a timestamp and hold a full day"
```

---

## Task 2: the page's pure builders — rows, labels, detail cells

**Files:**
- Create: `webgui/pages/options/flow.py`
- Create: `webgui/tests/test_flow_page.py`

**Step 1: Write the failing tests**

Create `webgui/tests/test_flow_page.py`:

```python
"""Flow Alerts page — pure builders. Tier-1 reader of cache:options:flow_alerts."""
import datetime as dt
from zoneinfo import ZoneInfo

from pages.options import flow

CT = ZoneInfo("America/Chicago")

_XO = {"type": "crossover", "side": "calls_over", "symbol": "SPY", "ts": 1754750000,
       "call_prem": 1200000.0, "put_prem": 400000.0,
       "id": "SPY|crossover|calls_over|1754750000", "text": "SPY — call premium overtook puts"}
_UOA = {"type": "uoa", "side": "call", "symbol": "QQQ", "strike": 737.0,
        "expiry": "2026-08-09", "dte": 0, "cost": 1.72, "volume": 12400, "oi": 1100,
        "vol_oi": 11.27, "premium": 2132800.0, "ts": 1754750100,
        "id": "QQQ|uoa|call|737|2026-08-09", "text": "QQQ 0DTE 737C — UNUSUAL"}
_GF = {"type": "gamma_flip", "side": "to_negative", "symbol": "$SPX", "spot": 6412.0,
       "flip": 6400.0, "ts": 1754750400,
       "id": "$SPX|gamma_flip|to_negative|1754750400", "text": "$SPX — gamma flipped NEGATIVE"}
_VIEW = {"date": "2026-08-09", "alerts": [_XO, _UOA, _GF]}


def test_alert_rows_are_newest_first():
    """The service appends oldest-first; a tape reads newest-first."""
    rows = flow.alert_rows(_VIEW)
    assert [r["symbol"] for r in rows] == ["$SPX", "QQQ", "SPY"]


def test_alert_rows_survive_malformed_input():
    """render() does no validation, so the builder must be total."""
    assert flow.alert_rows(None) == []
    assert flow.alert_rows({}) == []
    assert flow.alert_rows({"alerts": "nope"}) == []
    assert flow.alert_rows({"alerts": [None, {}, {"type": "uoa"}]}) != []  # degrades, no raise


def test_alert_rows_stamp_the_raw_kind_key_for_filtering():
    """Filters work off the raw type key, not the display label."""
    assert {r["_kind_key"] for r in flow.alert_rows(_VIEW)} == {
        "crossover", "uoa", "gamma_flip"}


def test_kind_labels_are_whole_words():
    """UI labels spell things out; 'UOA' means nothing at a glance."""
    assert flow.alert_kind_label(_XO) == "Crossover"
    assert flow.alert_kind_label(_UOA) == "Unusual activity"
    assert flow.alert_kind_label(_GF) == "Gamma flip"
    assert flow.alert_kind_label({}) == "Flow"


def test_side_labels_read_directionally():
    assert flow.side_label(_XO) == "Calls over"
    assert flow.side_label(_UOA) == "Call"
    assert flow.side_label(_GF) == "To negative"


def test_detail_cells_are_type_specific():
    assert flow.alert_detail(_XO) == "$1.20M calls vs $400k puts"
    assert flow.alert_detail(_UOA) == "0DTE 737C · 12,400 vol / 1,100 OI (11.3×) · $2.13M"
    assert flow.alert_detail(_GF) == "spot 6412 vs flip 6400"
    assert flow.alert_detail({"type": "uoa"}) == ""   # missing fields, no raise


def test_tone_class_maps_direction_to_a_fixed_palette_class():
    """Tailwind-first: a finite (type, side) set maps to static classes, never a
    computed color or an inline style."""
    assert "emerald" in flow.tone_class(_XO)
    assert "rose" in flow.tone_class(_GF)
    assert flow.tone_class({}) == flow._TONE_NEUTRAL
```

**Step 2: Run to verify it fails**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_flow_page.py -q`

Expected: collection error — `ModuleNotFoundError: pages.options.flow`.

**Step 3: Write the implementation**

Create `webgui/pages/options/flow.py`. Study `webgui/pages/options/matrix.py` first — it is the closest template (Tier-1 reader, pure builders, stamped `_*_class` fields, version-poll). Write only what the tests above need:

```python
"""Flow Alerts — Tier-1 reader of cache:options:flow_alerts.

The options service detects three kinds of flow alert on each 1-min GEX tick
(premium crossover, contract-level unusual activity, dealer gamma-regime flip),
pushes them to the phone, and publishes a day-scoped rolling list. Until this
page existed the webgui only chimed and toasted them, so a missed toast meant a
lost alert. This is the durable view: today's alerts, newest first.

Pure builders are module-level and NiceGUI-free for testing; ``render()`` mounts
the table and version-polls the bus. Tier-1: imports ONLY ``nicegui`` +
``bus_client`` + ``pages.*`` helpers — no engine/service imports.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

VIEW = "options:flow_alerts"

_CT_TZ = ZoneInfo("America/Chicago")

_KIND_LABEL = {"crossover": "Crossover", "uoa": "Unusual activity",
               "gamma_flip": "Gamma flip"}
_SIDE_LABEL = {"calls_over": "Calls over", "puts_over": "Puts over",
               "call": "Call", "put": "Put",
               "to_positive": "To positive", "to_negative": "To negative"}

# Direction → a FIXED Tailwind class (the finite-set mapping the UI standard
# requires). Bullish reads green, bearish red, everything else neutral.
_TONE_POS = "text-emerald-400"
_TONE_NEG = "text-rose-400"
_TONE_NEUTRAL = "text-slate-300"
_TONE = {
    ("crossover", "calls_over"): _TONE_POS,
    ("crossover", "puts_over"): _TONE_NEG,
    ("uoa", "call"): _TONE_POS,
    ("uoa", "put"): _TONE_NEG,
    # A gamma flip to POSITIVE means dealers dampen volatility (calmer), to
    # NEGATIVE means they amplify it — read as constructive vs risky rather than
    # bullish vs bearish, but the same two colors carry it.
    ("gamma_flip", "to_positive"): _TONE_POS,
    ("gamma_flip", "to_negative"): _TONE_NEG,
}


def alert_kind_label(a):
    return _KIND_LABEL.get((a or {}).get("type"), "Flow")


def side_label(a):
    return _SIDE_LABEL.get((a or {}).get("side"), "")


def tone_class(a):
    d = a or {}
    return _TONE.get((d.get("type"), d.get("side")), _TONE_NEUTRAL)


def _money(v):
    """Compact dollars: $2.13M / $400k / $912. '' when unusable."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    a = abs(v)
    if a >= 999_500:
        return f"${v/1e6:.2f}M"
    if a >= 1e3:
        return f"${v/1e3:.0f}k"
    return f"${v:,.0f}"


def _exp_short(expiry, dte):
    if dte == 0:
        return "0DTE"
    try:
        _, m, d = str(expiry).split("-")
        return f"{int(m):02d}/{int(d):02d}"
    except Exception:
        return str(expiry or "")


def alert_detail(a):
    """The type-specific detail cell. Total — missing fields yield ''."""
    d = a or {}
    t = d.get("type")
    try:
        if t == "crossover":
            cp, pp = _money(d.get("call_prem")), _money(d.get("put_prem"))
            if not cp or not pp:
                return ""
            return f"{cp} calls vs {pp} puts"
        if t == "uoa":
            if d.get("strike") is None or d.get("volume") is None:
                return ""
            cp = "C" if d.get("side") == "call" else "P"
            return (f"{_exp_short(d.get('expiry'), d.get('dte'))} {float(d['strike']):g}{cp} · "
                    f"{int(d['volume']):,} vol / {int(d.get('oi') or 0):,} OI "
                    f"({float(d.get('vol_oi') or 0):.1f}×) · {_money(d.get('premium'))}")
        if t == "gamma_flip":
            spot, flip = d.get("spot"), d.get("flip")
            if spot is None or flip is None:
                return ""
            return f"spot {float(spot):g} vs flip {float(flip):g}"
    except (TypeError, ValueError):
        return ""
    return ""


def alert_rows(view):
    """Display rows, NEWEST FIRST. The service appends oldest-first.

    Total over a missing/malformed view — ``render()`` does no validation."""
    alerts = (view or {}).get("alerts") if isinstance(view, dict) else None
    if not isinstance(alerts, list):
        return []
    rows = []
    for a in alerts:
        if not isinstance(a, dict):
            continue
        ts = a.get("ts")
        rows.append({
            "id": a.get("id") or f"{a.get('symbol', '')}|{len(rows)}",
            "ts": ts if isinstance(ts, (int, float)) else None,
            "time": fmt_time(ts),
            "age": "",                      # filled by the live age tick
            "symbol": a.get("symbol", ""),
            "kind": alert_kind_label(a),
            "_kind_key": a.get("type") or "",
            "side": side_label(a),
            "detail": alert_detail(a),
            "text": a.get("text", ""),
            "_tone_class": tone_class(a),
        })
    rows.reverse()
    return rows
```

`fmt_time` arrives in Task 3. Write Task 3's tests next and implement `fmt_time`/`age_text` before running either task's suite — the module won't import until `fmt_time` exists.

**Step 4: Run the tests to verify they pass**

(After Task 3's `fmt_time` exists.) Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_flow_page.py -q`

Expected: all pass.

**Step 5: Commit**

```bash
git add webgui/pages/options/flow.py webgui/tests/test_flow_page.py
git commit -m "feat(flow): pure row/label/detail builders for the Flow Alerts page"
```

---

## Task 3: time, age, filtering and status text

**Files:**
- Modify: `webgui/pages/options/flow.py`
- Modify: `webgui/tests/test_flow_page.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_flow_page.py`:

```python
def test_fmt_time_renders_central_clock():
    """Trading times are Central everywhere in this app; ts is unix seconds."""
    ts = dt.datetime(2026, 8, 9, 9, 32, 5, tzinfo=CT).timestamp()
    assert flow.fmt_time(ts) == "09:32:05"
    assert flow.fmt_time(None) == ""
    assert flow.fmt_time("nope") == ""


def test_age_text_reads_at_a_glance():
    now = dt.datetime(2026, 8, 9, 10, 0, 0, tzinfo=CT)

    def t(**kw):
        return (now - dt.timedelta(**kw)).timestamp()

    assert flow.age_text(t(seconds=20), now) == "just now"
    assert flow.age_text(t(minutes=2), now) == "2m ago"
    assert flow.age_text(t(minutes=74), now) == "1h 14m ago"
    assert flow.age_text(None, now) == ""


def test_age_text_never_reads_negative_on_clock_skew():
    """A ts a few seconds in the future (service/GUI clock skew) must not render
    '-1m ago'."""
    now = dt.datetime(2026, 8, 9, 10, 0, 0, tzinfo=CT)
    assert flow.age_text((now + dt.timedelta(seconds=30)).timestamp(), now) == "just now"


def test_filter_rows_by_kind_and_symbol():
    rows = flow.alert_rows(_VIEW)
    assert len(flow.filter_rows(rows, {"crossover"}, None)) == 1
    assert len(flow.filter_rows(rows, {"crossover", "uoa"}, None)) == 2
    assert [r["symbol"] for r in flow.filter_rows(rows, None, "QQQ")] == ["QQQ"]
    # No kinds selected shows nothing -- an explicit empty selection, not "all".
    assert flow.filter_rows(rows, set(), None) == []
    # None means unfiltered.
    assert len(flow.filter_rows(rows, None, None)) == 3


def test_symbol_options_are_sorted_and_deduped():
    assert flow.symbol_options(flow.alert_rows(_VIEW)) == ["$SPX", "QQQ", "SPY"]


def test_status_text_distinguishes_quiet_from_cold():
    """'Nothing has fired' and 'the service isn't publishing' look identical on an
    empty table -- they must not read the same."""
    assert flow.status_text(None) == "Waiting for the options service…"
    assert "No flow alerts yet" in flow.status_text({"date": "2026-08-09", "alerts": []})
    assert flow.status_text(_VIEW) == "3 alerts today · 2026-08-09"
    assert flow.status_text({"date": "2026-08-09", "alerts": [_XO]}) == "1 alert today · 2026-08-09"
```

**Step 2: Run to verify it fails**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_flow_page.py -q`

Expected: `AttributeError: module 'pages.options.flow' has no attribute 'fmt_time'`.

**Step 3: Implement**

Add to `flow.py`:

```python
def fmt_time(ts):
    """Unix seconds → Central 'HH:MM:SS'. '' when unusable."""
    if not isinstance(ts, (int, float)):
        return ""
    try:
        return _dt.datetime.fromtimestamp(ts, tz=_CT_TZ).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def age_text(ts, now):
    """How long ago the alert fired: 'just now' / '2m ago' / '1h 14m ago'.

    Clamped at zero — a ts slightly in the future (service/GUI clock skew) reads
    'just now', never a negative age."""
    if not isinstance(ts, (int, float)):
        return ""
    try:
        secs = now.timestamp() - float(ts)
    except (TypeError, ValueError, OverflowError):
        return ""
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m ago"
    return f"{mins // 60}h {mins % 60}m ago"


def filter_rows(rows, kinds, symbol):
    """Rows matching the selected kind keys and symbol.

    ``kinds=None`` and ``symbol=None`` mean unfiltered; an EMPTY kinds set means
    the user deselected everything and should see nothing."""
    out = []
    for r in rows or []:
        if kinds is not None and r.get("_kind_key") not in kinds:
            continue
        if symbol and r.get("symbol") != symbol:
            continue
        out.append(r)
    return out


def symbol_options(rows):
    """Sorted distinct symbols present in today's alerts (for the filter dropdown)."""
    return sorted({r.get("symbol") for r in rows or [] if r.get("symbol")})


def status_text(view):
    """Status line. Distinguishes a quiet day from a service that isn't publishing —
    on an empty table those look identical otherwise."""
    if not isinstance(view, dict) or not view:
        return "Waiting for the options service…"
    n = len(view.get("alerts") or [])
    date = view.get("date") or ""
    if not n:
        return f"No flow alerts yet today · {date}".rstrip(" ·")
    return f"{n} alert{'' if n == 1 else 's'} today · {date}".rstrip(" ·")
```

**Step 4: Run to verify it passes**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_flow_page.py -q`

Expected: all pass (Task 2's tests included).

**Step 5: Commit**

```bash
git add webgui/pages/options/flow.py webgui/tests/test_flow_page.py
git commit -m "feat(flow): time, live age, filtering and status text"
```

---

## Task 4: the handoff to Dealer Positioning

A row click must open `/options/gamma` with that symbol loaded.

**Files:**
- Modify: `webgui/pages/options/handoff.py:18-19` (the `_pending` dict) and append the new functions
- Modify: `webgui/pages/options/gamma.py:2644-2648` (the build-time symbol sync)
- Test: `webgui/tests/test_flow_page.py` (append)

**Step 1: Write the failing test**

```python
def test_gamma_handoff_is_one_shot():
    """A stashed symbol must be consumed exactly once, or navigating back to
    Dealer Positioning later would silently re-hijack the dropdown."""
    from pages.options import handoff
    handoff.set_pending_gamma("QQQ")
    assert handoff.take_pending_gamma() == "QQQ"
    assert handoff.take_pending_gamma() is None
```

**Step 2: Run to verify it fails**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_flow_page.py -q -k gamma_handoff`

Expected: `AttributeError: … has no attribute 'set_pending_gamma'`.

**Step 3: Implement the handoff**

In `handoff.py`, add `"gamma"` to the `_pending` dict:

```python
_pending = {"calculator": None, "expected_move": None,
            "simulator": None, "calculator_legs": None, "gamma": None}
```

and append, following the module's existing four-function pattern:

```python
def set_pending_gamma(symbol):
    _pending["gamma"] = symbol


def take_pending_gamma():
    """Return and clear the pending Dealer Positioning symbol (one-shot)."""
    s = _pending.get("gamma")
    _pending["gamma"] = None
    return s


def send_to_gamma(symbol):
    """Stash a symbol and open Dealer Positioning on it (same browser tab)."""
    if not symbol:
        ui.notify("No symbol for dealer positioning.", type="warning")
        return
    set_pending_gamma(symbol)
    ui.navigate.to("/options/gamma")
```

**Step 4: Wire the consumption in gamma.py**

At `gamma.py:2644-2648` the page already points the dropdown at the cached symbol **before** wiring `on_value_change` (so the programmatic set doesn't fire a spurious refresh). Let a pending symbol win, then request one refresh so the cached snapshot follows:

```python
        # Sync the dropdown to the symbol actually in the cache so a page (re)build
        # doesn't show $SPX while another symbol's data is displayed (which a later
        # refresh would then revert to $SPX). Done BEFORE wiring on_value_change.
        # A symbol handed over from the Flow Alerts page wins over the cached one —
        # it is an explicit request, and the refresh below moves the cache to it.
        from .handoff import take_pending_gamma
        _handoff_sym = take_pending_gamma()
        _set_symbol(_handoff_sym or (state["snap"] or {}).get("symbol"))
        symbol_in.on_value_change(lambda e: _on_symbol_change())
        _render_view()
        if _handoff_sym:
            _request_refresh()
```

`_request_refresh` is `@guard`ed and reads the dropdown via `_current_symbol()`, so it picks up the symbol just set.

**Step 5: Run the tests**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_flow_page.py webgui/tests/test_shell.py -q`

Expected: all pass (the shell suite imports gamma, so a syntax or import error there shows up here).

**Step 6: Commit**

```bash
git add webgui/pages/options/handoff.py webgui/pages/options/gamma.py webgui/tests/test_flow_page.py
git commit -m "feat(flow): hand a symbol from a flow alert to Dealer Positioning"
```

---

## Task 5: render() — the table, filters and live age

**Files:**
- Modify: `webgui/pages/options/flow.py`
- Modify: `webgui/tests/test_flow_page.py`

**Step 1: Write `flow_columns` and its test**

Append the test:

```python
def test_flow_columns_are_sortable_and_ordered():
    names = [c["name"] for c in flow.flow_columns()]
    assert names == ["time", "age", "symbol", "kind", "side", "detail", "text"]
    assert all(c["sortable"] for c in flow.flow_columns())
```

Run it, watch it fail, then implement:

```python
def flow_columns():
    spec = [("time", "Time"), ("age", "Age"), ("symbol", "Symbol"),
            ("kind", "Type"), ("side", "Side"), ("detail", "Detail"),
            ("text", "Alert")]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]
```

**Step 2: Write `render()`**

There is no unit test for `render()` itself (it is thin wiring; `test_shell.py`'s route smoke covers that it builds). Model it closely on `matrix.py:193-251`:

```python
def render():
    """Build the Flow Alerts page: today's alerts newest-first, version-polling
    ``cache:options:flow_alerts``.

    Tier-1 (engine-free). Two cadences share one 2 s timer: the payload is re-read
    only when the cache VERSION moves, while the Age column is recomputed every
    tick against the rows already on screen — so age stays live without churning
    the table."""
    import bus_client
    from nicegui import run, ui

    from pages.ui_guard import guard, guard_async

    from .handoff import send_to_gamma
    from .theme import CARD, EYEBROW, LABEL, PAGE, QUASAR_INTERNAL_CSS

    ui.add_css(QUASAR_INTERNAL_CSS)
    state = {"version": None, "rows": [], "kinds": set(_KIND_LABEL), "symbol": None}

    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-4"):
        with ui.column().classes(f"{CARD} w-full gap-2"):
            ui.label("Flow Alerts").classes(f"text-h6 {LABEL}")
            ui.label("Today's options-flow alerts, newest first").classes(EYEBROW)

            with ui.row().classes("items-center gap-3 flex-wrap pt-1"):
                kind_sel = ui.select(
                    dict(_KIND_LABEL), value=list(_KIND_LABEL), multiple=True,
                    label="Type").classes("w-64").props("dense outlined use-chips")
                symbol_sel = ui.select(["All"], value="All", label="Symbol") \
                    .classes("w-40").props("dense outlined")

            status = ui.label("Waiting for the options service…").classes(EYEBROW)
            table = ui.table(columns=flow_columns(), rows=[], row_key="id",
                             pagination={"rowsPerPage": 0}) \
                .classes("w-full flow-table").props("dense")
            table.add_slot("body-cell-side", _TONE_SLOT)
            table.add_slot("body-cell-text", _TONE_SLOT)

    def _apply_filters():
        table.rows = filter_rows(state["rows"], state["kinds"], state["symbol"])
        table.update()

    def _tick_age():
        now = _dt.datetime.now(tz=_CT_TZ)
        for r in state["rows"]:
            r["age"] = age_text(r.get("ts"), now)
        _apply_filters()

    def _paint(payload):
        state["rows"] = alert_rows(payload)
        opts = ["All"] + symbol_options(state["rows"])
        if list(symbol_sel.options) != opts:
            symbol_sel.options = opts
            if state["symbol"] not in opts:
                state["symbol"] = None
                symbol_sel.value = "All"
            symbol_sel.update()
        status.text = status_text(payload)
        _tick_age()

    @guard
    def _on_kind_change(e):
        state["kinds"] = set(e.value or [])
        _apply_filters()

    @guard
    def _on_symbol_change(e):
        state["symbol"] = None if e.value in (None, "All") else e.value
        _apply_filters()

    @guard
    def _on_row_click(e):
        row = e.args[1] if len(e.args) > 1 else None
        send_to_gamma((row or {}).get("symbol"))

    kind_sel.on_value_change(_on_kind_change)
    symbol_sel.on_value_change(_on_symbol_change)
    table.on("rowClick", _on_row_click)

    @guard_async
    async def _poll():
        # Cheap :ver probe off the loop; the full payload read only on a change.
        v = await run.io_bound(bus_client.read_version, VIEW)
        if v is not None and v != state["version"]:
            payload = await run.io_bound(bus_client.read, VIEW)
            if payload:
                state["version"] = v
                _paint(payload)
                return
        _tick_age()      # no new data — just keep the ages honest

    payload, version = bus_client.read_full(VIEW)
    if payload:
        state["version"] = version
        _paint(payload)
    ui.timer(2.0, _poll)
```

Add the slot near the other module constants — **`:class`, never `:style`**:

```python
# Colored cells bind the stamped ``_tone_class`` field via :class (Tailwind-first
# — no inline style). Raw <q-td> template, the scanner.py add_slot idiom.
_TONE_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._tone_class">{{ props.value }}</span>
  </q-td>
'''
```

**Step 3: Run the page tests**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_flow_page.py -q`

Expected: all pass.

**Step 4: Commit**

```bash
git add webgui/pages/options/flow.py webgui/tests/test_flow_page.py
git commit -m "feat(flow): render the Flow Alerts table with filters and live age"
```

---

## Task 6: nav wiring — route, rail item, favicon color, help

**Files:**
- Modify: `webgui/main.py:298-301` (`OPTIONS_RAIL`), `:474-490` (`_TAB_COLOR`), and the route block near `:1320`
- Modify: `webgui/page_help.py`
- Modify: `webgui/tests/test_shell.py:14-24` (route set), `:396` (breadcrumb), `:874-875` (rail assertion), `:482-495` (icon docstring)
- Modify: `webgui/tests/test_no_inline_style.py:67`

**Step 1: Write the failing tests**

In `webgui/tests/test_shell.py`, add `"/options/flow"` to the expected route set (line ~18) and extend the rail assertion (line ~875):

```python
    assert [r for r, _l, _i in main.OPTIONS_RAIL] == [
        "/options/gamma", "/options/matrix", "/options/flow"]
```

Add a breadcrumb assertion next to the existing rail ones (~line 396):

```python
    assert main.breadcrumb_parts("/options/flow") == ("Flow Alerts", "")
```

In `webgui/tests/test_no_inline_style.py`, extend the guard list so the new page is covered:

```python
# Tier-1 reader pages: Tailwind tokens + colored-cell slots binding stamped
# `_*_class` fields (no `.style(`, no Vue `:style=`).
OPTIONS_MATRIX_FILES = ["matrix.py", "flow.py"]
```

**Step 2: Run to verify they fail**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui/tests/test_shell.py webgui/tests/test_no_inline_style.py -q`

Expected: failures on the missing route, the rail list, and the missing `flow.py`.

**Step 3: Wire it up in `main.py`**

Add the rail item:

```python
OPTIONS_RAIL = [
    ("/options/gamma", "Dealer Positioning", "stacked_line_chart"),
    ("/options/matrix", "Opportunity Board", "grid_on"),
    ("/options/flow", "Flow Alerts", "bolt"),
]
```

Add a favicon color — pick one **not already in `_TAB_COLOR`** (check the dict; `#ff7043` deep orange is free at time of writing):

```python
    "/options/flow": "#ff7043",           # Flow Alerts — deep orange
```

Add the route beside the matrix one (~line 1320):

```python
@ui.page("/options/flow")
def options_flow_page() -> None:
    with _layout("/options/flow", "Flow Alerts"):
        from pages.options import flow
        flow.render()
```

**Step 4: Fix the icon-test docstring**

`test_drawer_icons_are_present_and_distinct` (line ~482) claims "9 drawer items (3 groups + the 3 OPTIONS_RAIL pages + the 3 FLAT_NAV pages)". That was already stale — there are 4 groups and were 2 rail pages. With this change it is **4 groups + 3 rail + 3 flat = 10**. Correct the docstring. The assertion itself is a `Counter` distinctness check and needs no change, but confirm `bolt` collides with nothing.

**Step 5: Add the page help**

In `webgui/page_help.py`, beside the `/options/matrix` entry, add plain-English help in that file's voice:

```python
    "/options/flow": """
**Flow Alerts — the simple version**

Everything the options service flagged **today**, newest first — the alerts that
also chime and hit your phone, kept somewhere you can actually read them.

Three kinds:
- **Crossover** — call premium overtook put premium for a symbol, or the reverse.
- **Unusual activity** — one contract traded far more than its open interest.
- **Gamma flip** — spot crossed the dealer gamma flip level, so dealer hedging
  starts damping volatility instead of amplifying it (or the reverse).

**Age** tells you whether you're looking at something that just happened or at
this morning's news. **Click any row** to open Dealer Positioning for that symbol.

The list covers today only and resets overnight.
""",
```

**Step 6: Run the full webgui suite**

Run: `cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui -q`

Expected: all pass (1053+ tests). Any failure here is yours.

**Step 7: Commit**

```bash
git add webgui/main.py webgui/page_help.py webgui/tests/test_shell.py webgui/tests/test_no_inline_style.py
git commit -m "feat(flow): add the Flow Alerts rail page to the nav"
```

---

## Task 7: verify against real data

Unit tests prove the builders; they do not prove the page reads what the service actually publishes. Verify with a real payload before claiming this works.

**Step 1: Check whether today's key has data**

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -c "import sys; sys.path.insert(0,'webgui'); import bus_client; v=bus_client.read('options:flow_alerts'); print(type(v)); print((v or {}).get('date')); a=(v or {}).get('alerts') or []; print(len(a), 'alerts'); print(a[-1] if a else 'none')"
```

Note `bus_client.read` already unwraps the cache envelope — you get the payload dict, not a `CacheEnvelope`. (Getting this backwards has bitten this codebase before: `Bus.cache_get` returns an envelope and needs `.payload`.)

**Step 2: Drive the builders with that real payload**

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -c "import sys,datetime as dt; sys.path.insert(0,'webgui'); import bus_client; from pages.options import flow; v=bus_client.read('options:flow_alerts'); rows=flow.alert_rows(v); print(flow.status_text(v)); now=dt.datetime.now(tz=flow._CT_TZ); [print(r['time'], flow.age_text(r['ts'], now), r['symbol'], r['kind'], r['side'], '|', r['detail']) for r in rows[:15]]"
```

Confirm: rows are newest-first, every row has a Time (including unusual-activity rows — that is Task 1's fix, and it only applies to alerts published *after* an `options_svc` restart; older ones legitimately show blank), details read correctly per type, and ages look sane.

**If the key is empty** (weekend, off-hours, or the service is down), say so plainly rather than claiming verification. Fall back to feeding a hand-built payload of all three alert types through the same builders, and note in your report that live data was unavailable.

**Step 3: Report honestly**

State what you ran, what you saw, and what remains unverified — specifically that browser rendering, the row-click handoff, and the live age tick are **not** covered by tests and need a look in the running app.

---

## Task 8: update CLAUDE.md and finish

**Files:**
- Modify: `CLAUDE.md` (routes table + the `pages/options/` helper paragraph)
- Modify: `docs/CHANGELOG.md` (new dated entry at the top)

**Step 1: Add the route-table row**

In `CLAUDE.md`'s routes table, after the `/options/matrix` row:

```markdown
| `/options/flow` | Flow Alerts (**NEW 2026-08-09** — a **main-menu (left-rail) item under the Options group** (`main.OPTIONS_RAIL`, standalone page, no tab strip): the durable view of today's options-flow alerts, which until now only chimed and toasted. Pure Tier-1 reader of **`cache:options:flow_alerts`** (`webgui/pages/options/flow.py`), version-polling ~2 s: a chronological table **newest first** — Time (CT) / **Age** / Symbol / Type / Side / Detail / Alert — over the three detector types (**Crossover** premium lead flip · **Unusual activity** contract vol-vs-OI · **Gamma flip** spot crossing the dealer flip), tinted green/red from a finite `(type, side)` → Tailwind class map. Kind + symbol filters run **client-side** over already-read rows, so toggling is instant; the **Age** column re-computes on every 2 s tick against the rows on screen while the payload is re-read only on a version change. **Row click → Dealer Positioning for that symbol** (`handoff.send_to_gamma` + a one-shot stash consumed at `gamma.render()`'s build-time symbol sync, so the handed symbol beats the cached one and triggers one refresh). Two Tier-2 lines came with it: `_FLOW_ALERTS_MAX` **50 → 300** (50 dropped the morning's alerts on a busy day) and a **`ts` stamped on UOA alerts**, which `detect_uoa` never emitted — so unusual-activity rows had no time at all. **Today only**, resets overnight; no badge, no history) | built |
```

**Step 2: Note the helper additions**

In the `pages/options/` shared-helpers paragraph, extend the `handoff.py` description to mention the gamma stash (`send_to_gamma`/`take_pending_gamma`), alongside the existing calculator/simulator/expected-move ones.

**Step 3: Add the changelog entry**

Newest-first at the top of `docs/CHANGELOG.md`, in that file's established voice: what shipped, why (alerts were detected and pushed but never displayed), the two `options_svc` changes, and the operational note that **`options_svc` and the webgui both need a restart** — UOA timestamps only appear on alerts published after the service restarts.

**Step 4: Run both suites one final time**

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest webgui -q
```

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/stoic-saha-cb9613" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q
```

Expected: webgui fully green; options_svc green except its 2 pre-existing date-relative `test_expected_move` failures.

**Step 5: Commit**

```bash
git add CLAUDE.md docs/CHANGELOG.md
git commit -m "docs(flow): record the Flow Alerts screen"
```

**Step 6: Hand back**

Report: what shipped, both suite results (with the baseline failures named as pre-existing), what was verified against live data and what was not, and the deployment note — **restart `options_svc` and the webgui**, then verify in the **dev** checkout before promoting, per THE DEVELOPMENT RULE.
