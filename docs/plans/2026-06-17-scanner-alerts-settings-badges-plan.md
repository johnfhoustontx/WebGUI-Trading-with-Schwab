# Scanner Alerts + Settings + Nav Badges Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add app-wide audio + desktop alerts on new scanner signals, a JSON-backed Settings page, new/unread nav badges (Scanner/Captured/Driver), and a modernized nav drawer — all in the GUI tier.

**Architecture:** A pure JSON settings store (`app_settings.py`) and pure alert/badge helpers (`alerts.py`) are unit-tested in isolation. A `ui.timer` added to the shared `_layout` runs the watcher on every page (app-wide): it reads bus versions for `options:scan`/`options:captured`/`driver:approvals`, recomputes module-level badge counts, and plays a bundled WAV (+ optional `Notification`) when new qualifying signals appear. The Settings page binds controls to `app_settings`. Drawer restyle is scoped CSS + a trailing badge in `_nav_link`.

**Tech Stack:** NiceGUI 3.x (`ui.timer`, `ui.run_javascript`, `app.add_static_files`, `q-badge` slots), Python stdlib (`json`, `wave`, `zoneinfo`), pytest. GUI imports stay limited to `nicegui` + `bus_client` + page modules.

---

## Conventions for this plan

- Run tests from the `webgui/` folder: `cd webgui && ..\.venv\Scripts\python -m pytest <args>`.
- Reuse `scanner._sig_key` for signal identity — do **not** duplicate it.
- Module-level single-user state mirrors the existing `_NAV_OPEN`/`_CACHE` pattern.
- Commit after each task. End commit messages with the `Co-Authored-By` trailer.

---

### Task 1: Settings store — `webgui/app_settings.py`

**Files:**
- Create: `webgui/app_settings.py`
- Test: `webgui/tests/test_app_settings.py`

**Step 1: Write the failing tests**

```python
# webgui/tests/test_app_settings.py
import json
import app_settings


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "_PATH", tmp_path / "settings.json")


def test_load_returns_defaults_when_no_file(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert app_settings.load() == app_settings.DEFAULTS


def test_set_persists_and_get_reads_back(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    app_settings.set("alert_enabled", False)
    assert app_settings.get("alert_enabled") is False
    # round-trips through disk
    assert json.loads((tmp_path / "settings.json").read_text())["alert_enabled"] is False


def test_load_merges_partial_file_over_defaults(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text(json.dumps({"alert_volume": 0.2}))
    loaded = app_settings.load()
    assert loaded["alert_volume"] == 0.2
    assert loaded["alert_sound"] == app_settings.DEFAULTS["alert_sound"]  # default kept


def test_load_falls_back_on_garbage_file(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text("{not json")
    assert app_settings.load() == app_settings.DEFAULTS


def test_get_unknown_key_returns_none(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert app_settings.get("nope") is None
```

**Step 2: Run to verify they fail**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_app_settings.py -q`
Expected: FAIL (module `app_settings` not found).

**Step 3: Write the implementation**

```python
# webgui/app_settings.py
"""JSON-backed, single-user GUI settings store (pure stdlib, unit-testable).

Persists to webgui/data/settings.json (data/ is gitignored — regenerates from
DEFAULTS on a fresh clone). No engine imports; the GUI tier stays thin.
"""
import json
import pathlib

DEFAULTS = {
    "alert_enabled": True,
    "alert_sound": "chime",          # chime | bell | ping
    "alert_volume": 0.6,             # 0.0–1.0
    "alert_market_hours_only": True,
    "alert_min_score": 0,            # only alert on signals with score >= this
    "desktop_notifications": False,
}

_PATH = pathlib.Path(__file__).resolve().parent / "data" / "settings.json"


def load():
    """Full settings dict: file values merged over DEFAULTS; DEFAULTS on any error."""
    try:
        raw = json.loads(_PATH.read_text())
        if not isinstance(raw, dict):
            raise ValueError("settings.json is not an object")
        return {**DEFAULTS, **raw}
    except Exception:
        return dict(DEFAULTS)


def get(key):
    """Single setting value (None for unknown keys)."""
    return load().get(key)


def all():
    """Alias for load() — the full merged settings dict."""
    return load()


def set(key, value):
    """Persist one setting (writes the full merged dict back to disk)."""
    data = load()
    data[key] = value
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2))
    return data
```

**Step 4: Run to verify pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_app_settings.py -q`
Expected: PASS (5 tests).

**Step 5: Commit**

```bash
git add webgui/app_settings.py webgui/tests/test_app_settings.py
git commit -m "feat(webgui): JSON-backed app_settings store for GUI prefs"
```

---

### Task 2: Bundled alert sounds + static mount

**Files:**
- Create: `webgui/static/sounds/{chime,bell,ping}.wav`
- Create: `webgui/tools/gen_sounds.py` (generator, documents how the WAVs were made)
- Modify: `webgui/main.py` (mount `/static`)
- Test: `webgui/tests/test_static_sounds.py`

**Step 1: Write the generator** (`webgui/tools/gen_sounds.py`)

```python
"""Generate the three short alert WAVs into webgui/static/sounds/.

Run once: ..\..\.venv\Scripts\python tools/gen_sounds.py  (from webgui/)
Pure stdlib (wave + math) so the assets can be regenerated deterministically.
"""
import math
import pathlib
import struct
import wave

OUT = pathlib.Path(__file__).resolve().parents[1] / "static" / "sounds"
RATE = 44100

# name -> list of (freq_hz, seconds) segments (a tiny two-note motif each)
SOUNDS = {
    "chime": [(880, 0.12), (1320, 0.18)],
    "bell":  [(1568, 0.06), (1175, 0.30)],
    "ping":  [(2093, 0.10)],
}


def _samples(segments):
    for freq, dur in segments:
        n = int(RATE * dur)
        for i in range(n):
            env = math.sin(math.pi * i / n)            # smooth in/out envelope
            yield 0.5 * env * math.sin(2 * math.pi * freq * i / RATE)


def write_wav(path, segments):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767))
                          for s in _samples(segments))
        w.writeframes(frames)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, segs in SOUNDS.items():
        write_wav(OUT / f"{name}.wav", segs)
        print("wrote", OUT / f"{name}.wav")
```

**Step 2: Generate the files**

Run: `cd webgui && ..\.venv\Scripts\python tools/gen_sounds.py`
Expected: prints 3 paths; `static/sounds/{chime,bell,ping}.wav` exist (each a few KB).

**Step 3: Write the failing test**

```python
# webgui/tests/test_static_sounds.py
import pathlib
import main

SOUNDS = pathlib.Path(__file__).resolve().parents[1] / "static" / "sounds"


def test_three_alert_wavs_exist_and_nonempty():
    for name in ("chime", "bell", "ping"):
        p = SOUNDS / f"{name}.wav"
        assert p.exists() and p.stat().st_size > 200, f"missing/empty {name}.wav"


def test_static_route_mounted():
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert any(str(p).startswith("/static") for p in paths)
```

**Step 4: Run to verify the route test fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_static_sounds.py -q`
Expected: `test_static_route_mounted` FAILS (not mounted yet); the wav test passes.

**Step 5: Mount static files in `main.py`**

After `import proxy` (near line 23), add:

```python
_STATIC_DIR = _REPO_ROOT / "webgui" / "static"
if _STATIC_DIR.is_dir():
    app.add_static_files("/static", str(_STATIC_DIR))
```

**Step 6: Run to verify pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_static_sounds.py -q`
Expected: PASS (2 tests).

**Step 7: Commit**

```bash
git add webgui/static/sounds webgui/tools/gen_sounds.py webgui/main.py webgui/tests/test_static_sounds.py
git commit -m "feat(webgui): bundle alert sounds + mount /static"
```

---

### Task 3: Alert + badge pure helpers — `webgui/alerts.py`

**Files:**
- Create: `webgui/alerts.py`
- Test: `webgui/tests/test_alerts.py`

**Step 1: Write the failing tests**

```python
# webgui/tests/test_alerts.py
import datetime as dt
from zoneinfo import ZoneInfo
import alerts

CT = ZoneInfo("America/Chicago")


def _scan():
    return {
        "signals_0dte": [
            {"symbol": "SPX", "type": "PUT", "short_strike": 5000, "long_strike": 4990,
             "expiration": "2026-06-17", "composite_score": 80},
        ],
        "signals_swing": [
            {"symbol": "QQQ", "type": "CALL", "short_strike": 500, "long_strike": 510,
             "expiration": "2026-06-20", "composite_score": 40},
        ],
    }


def test_scanner_keys_covers_both_tables():
    assert len(alerts.scanner_keys(_scan())) == 2
    assert alerts.scanner_keys({}) == set()


def test_unread_count_is_current_minus_acked():
    keys = alerts.scanner_keys(_scan())
    assert alerts.unread_count(keys, set()) == 2
    one = {next(iter(keys))}
    assert alerts.unread_count(keys, one) == 1
    assert alerts.unread_count(keys, keys) == 0


def test_qualifying_new_respects_min_score_and_alerted():
    scan = _scan()
    # min_score 70 → only the 80-score SPX signal qualifies
    q = alerts.qualifying_new(scan, alerted=set(), min_score=70)
    assert len(q) == 1
    # already-alerted keys are excluded
    assert alerts.qualifying_new(scan, alerted=q, min_score=70) == set()
    # min_score 0 → both qualify
    assert len(alerts.qualifying_new(scan, alerted=set(), min_score=0)) == 2


def test_in_market_hours():
    assert alerts.in_market_hours(dt.datetime(2026, 6, 17, 10, 0, tzinfo=CT))   # Wed 10:00
    assert not alerts.in_market_hours(dt.datetime(2026, 6, 17, 16, 0, tzinfo=CT))  # after 15:00
    assert not alerts.in_market_hours(dt.datetime(2026, 6, 13, 10, 0, tzinfo=CT))  # Saturday


def test_should_alert_truth_table():
    now_open = dt.datetime(2026, 6, 17, 10, 0, tzinfo=CT)
    now_closed = dt.datetime(2026, 6, 17, 16, 0, tzinfo=CT)
    q = {"k"}
    base = {"alert_enabled": True, "alert_market_hours_only": True}
    assert alerts.should_alert(base, q, now_open)
    assert not alerts.should_alert(base, q, now_closed)         # gated by market hours
    assert not alerts.should_alert(base, set(), now_open)       # nothing new
    assert not alerts.should_alert({**base, "alert_enabled": False}, q, now_open)
    # market-hours gate off → fires even after close
    assert alerts.should_alert({**base, "alert_market_hours_only": False}, q, now_closed)
```

**Step 2: Run to verify they fail**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_alerts.py -q`
Expected: FAIL (module `alerts` not found).

**Step 3: Write the implementation**

```python
# webgui/alerts.py
"""Pure helpers for app-wide scanner alerts + nav badges (unit-tested).

The wiring (timer, audio element, badge UI) lives in main.py's _layout; this
module holds only the decision logic so it can be tested without NiceGUI.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from pages.options.scanner import _sig_key

CT = ZoneInfo("America/Chicago")
_OPEN, _CLOSE = dt.time(8, 0), dt.time(15, 0)   # CT trading window


def _signals(scan):
    scan = scan or {}
    return (scan.get("signals_0dte") or []) + (scan.get("signals_swing") or [])


def scanner_keys(scan):
    """Set of stable signal keys across both tables."""
    return {_sig_key(s) for s in _signals(scan)}


def scanner_scores(scan):
    """{signal_key: composite_score} across both tables."""
    return {_sig_key(s): (s.get("composite_score") or 0) for s in _signals(scan)}


def unread_count(current_keys, acked_keys):
    """How many current keys have not been acknowledged."""
    return len(set(current_keys) - set(acked_keys or set()))


def qualifying_new(scan, alerted, min_score):
    """Signal keys that are new (not yet alerted) AND score >= min_score."""
    scores = scanner_scores(scan)
    alerted = alerted or set()
    return {k for k, sc in scores.items() if k not in alerted and sc >= (min_score or 0)}


def in_market_hours(now):
    """True on a weekday within 08:00–15:00 CT (now is a tz-aware datetime)."""
    ct = now.astimezone(CT)
    return ct.weekday() < 5 and _OPEN <= ct.time() <= _CLOSE


def should_alert(settings, qualifying, now):
    """Whether to chime: enabled, something qualifies, and the market-hours gate passes."""
    if not settings.get("alert_enabled") or not qualifying:
        return False
    if settings.get("alert_market_hours_only") and not in_market_hours(now):
        return False
    return True
```

**Step 4: Run to verify pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_alerts.py -q`
Expected: PASS (5 tests).

**Step 5: Commit**

```bash
git add webgui/alerts.py webgui/tests/test_alerts.py
git commit -m "feat(webgui): pure alert/badge decision helpers"
```

---

### Task 4: Settings page + route + nav item

**Files:**
- Create: `webgui/pages/settings.py`
- Modify: `webgui/main.py` (route + nav item + FLAT_NAV)
- Modify: `webgui/tests/test_shell.py` (expect `/settings`)

**Step 1: Update the shell test (failing)**

In `webgui/tests/test_shell.py`, add `"/settings"` to the `expected` tuple (after `"/driver"`).

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_shell.py -q`
Expected: FAIL (`/settings` route missing).

**Step 2: Write the Settings page** (`webgui/pages/settings.py`)

```python
"""Settings page — GUI preferences (audio alerts, notifications).

Thin render(): each control writes through to app_settings. Extensible — add new
cards/sections here as more settings arrive.
"""
import app_settings
from nicegui import ui


def render():
    ui.label("Settings").classes("text-h5")
    s = app_settings.load()

    with ui.card().classes("w-full max-w-2xl"):
        ui.label("Scanner alerts").classes("text-subtitle1 font-bold")
        ui.label("Play a sound (and optional desktop notification) when new "
                 "scanner signals appear, on any page.").classes("opacity-70 text-sm")

        enable = ui.switch("Enable audio alert", value=s["alert_enabled"])
        enable.on_value_change(lambda e: app_settings.set("alert_enabled", e.value))

        with ui.row().classes("items-center gap-4"):
            sound = ui.select(["chime", "bell", "ping"], label="Sound",
                              value=s["alert_sound"]).classes("w-40")
            sound.on_value_change(lambda e: app_settings.set("alert_sound", e.value))
            test = ui.button("Test sound", icon="volume_up").props("outline")

        ui.label("Volume").classes("text-sm opacity-70")
        vol = ui.slider(min=0, max=1, step=0.05, value=s["alert_volume"]).classes("w-64")
        vol.on_value_change(lambda e: app_settings.set("alert_volume", e.value))

        mh = ui.switch("Only alert during market hours (08:00–15:00 CT, weekdays)",
                       value=s["alert_market_hours_only"])
        mh.on_value_change(lambda e: app_settings.set("alert_market_hours_only", e.value))

        with ui.row().classes("items-center gap-2"):
            ui.label("Minimum score to alert").classes("text-sm opacity-70")
            mscore = ui.number(value=s["alert_min_score"], min=0, max=100,
                               step=5).classes("w-28")
            mscore.on_value_change(lambda e: app_settings.set("alert_min_score", e.value or 0))

        ui.label("Tip: your browser blocks sound until you interact with the page — "
                 "clicking Test sound (or any nav link) unlocks it.").classes(
                 "opacity-60 text-xs")

    with ui.card().classes("w-full max-w-2xl"):
        ui.label("Desktop notifications").classes("text-subtitle1 font-bold")
        notif = ui.switch("Show a desktop notification too",
                          value=s["desktop_notifications"])
        notif.on_value_change(lambda e: app_settings.set("desktop_notifications", e.value))
        ui.button("Grant notification permission", icon="notifications").props("outline").on_click(
            lambda: ui.run_javascript("Notification && Notification.requestPermission()"))

    # Test sound uses the same shared audio element + helper as the live alert.
    def _test():
        from main import play_alert
        play_alert(app_settings.get("alert_sound"), app_settings.get("alert_volume"))
    test.on_click(_test)
```

**Step 3: Register route + nav item in `main.py`**

Add `"/settings"` to `FLAT_NAV`:

```python
FLAT_NAV = [
    ("/trade", "Trade", "analytics"),
    ("/portfolio", "Portfolio", "account_balance"),
    ("/driver", "Driver", "smart_toy"),
    ("/settings", "Settings", "settings"),
]
```

Add the page (next to the other `@ui.page` defs):

```python
@ui.page("/settings")
def settings_page() -> None:
    with _layout("/settings", "Settings"):
        from pages import settings
        settings.render()
```

**Step 4: Run the shell test**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_shell.py -q`
Expected: PASS. (`play_alert` is added in Task 5; the page only imports it inside the click handler, so import-time tests are unaffected. If you execute Task 5 first that's fine too.)

**Step 5: Commit**

```bash
git add webgui/pages/settings.py webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(webgui): Settings page + nav item"
```

---

### Task 5: Wire audio element, global watcher, and badges into `_layout`

**Files:**
- Modify: `webgui/main.py`

**Step 1: Add the `play_alert` helper + audio element**

Add a module-level helper near the top of `main.py` (after the static mount):

```python
def play_alert(sound: str, volume: float) -> None:
    """Play a bundled alert WAV in the connected browser at the given volume."""
    sound = sound if sound in ("chime", "bell", "ping") else "chime"
    vol = max(0.0, min(1.0, float(volume if volume is not None else 0.6)))
    ui.run_javascript(
        f"(() => {{ const a = document.getElementById('alert-audio'); if (!a) return; "
        f"a.src = '/static/sounds/{sound}.wav'; a.volume = {vol}; "
        f"a.play().catch(() => {{}}); }})()")


def notify_desktop(title: str, body: str) -> None:
    """Fire a desktop Notification if permission was granted (best-effort)."""
    safe = body.replace("'", "\\'")
    ui.run_javascript(
        "(() => { if (window.Notification && Notification.permission === 'granted') "
        f"new Notification('{title}', {{ body: '{safe}' }}); }})()")
```

**Step 2: Add the watcher into `_layout`**

Inside `_layout`, after the drawer/header/content are built and **before** `yield content`, create the hidden audio element and start the per-client watcher. Use `alerts` + `app_settings` and the module-level state below.

Add module-level state near `_NAV_OPEN`:

```python
import datetime as dt
from zoneinfo import ZoneInfo as _ZoneInfo
import alerts
import app_settings
import bus_client

_NAV_BADGES: dict[str, int] = {}
_ALERT_STATE: dict = {"acked_scan": set(), "alerted": set(),
                      "captured_ver": None, "captured_seen": None,
                      "driver_ver": None, "driver_seen": None}
_CT = _ZoneInfo("America/Chicago")
```

In `_layout`, after building the drawer, acknowledge the active route's badge and
keep a per-client map of badge label elements (created in `_nav_link`, see Task 6):

```python
    # Audio element used by play_alert (hidden, one per page/client).
    ui.html('<audio id="alert-audio" preload="auto"></audio>')

    # Acknowledge the badge for the page we're opening.
    _acknowledge(active)

    def _tick():
        _run_watcher()
        # Refresh visible badge labels for this client.
        for route, badge in _badge_refs.items():
            n = _NAV_BADGES.get(route, 0)
            badge.text = str(n) if n else ""
            badge.set_visibility(bool(n))
    ui.timer(2.0, _tick)
```

where `_badge_refs` is a dict populated by `_nav_link` (Task 6) and `_acknowledge`
/ `_run_watcher` are module-level:

```python
def _acknowledge(active: str) -> None:
    """Clear the badge for the page being viewed."""
    if active in ("/",):  # Scanner
        scan = bus_client.read("options:scan") or {}
        _ALERT_STATE["acked_scan"] = alerts.scanner_keys(scan)
    elif active == "/options/captured":
        _ALERT_STATE["captured_seen"] = bus_client.read_version("options:captured")
    elif active == "/driver":
        _ALERT_STATE["driver_seen"] = bus_client.read_version("driver:approvals")
    _recompute_badges()


def _recompute_badges() -> None:
    scan = bus_client.read("options:scan") or {}
    _NAV_BADGES["/"] = alerts.unread_count(
        alerts.scanner_keys(scan), _ALERT_STATE["acked_scan"])
    cap_ver = bus_client.read_version("options:captured")
    _NAV_BADGES["/options/captured"] = 1 if (
        cap_ver is not None and cap_ver != _ALERT_STATE["captured_seen"]) else 0
    drv = bus_client.read("driver:approvals") or {}
    drv_ver = bus_client.read_version("driver:approvals")
    pending = drv.get("status") == "pending"
    _NAV_BADGES["/driver"] = 1 if (
        pending and drv_ver != _ALERT_STATE["driver_seen"]) else 0


def _run_watcher() -> None:
    """One watcher tick: recompute badges + fire alerts on new qualifying signals."""
    _recompute_badges()
    scan = bus_client.read("options:scan") or {}
    s = app_settings.load()
    q = alerts.qualifying_new(scan, _ALERT_STATE["alerted"], s["alert_min_score"])
    now = dt.datetime.now(tz=_CT)
    if alerts.should_alert(s, q, now):
        play_alert(s["alert_sound"], s["alert_volume"])
        if s.get("desktop_notifications"):
            notify_desktop("New scanner signal",
                           f"{len(q)} new signal(s) meet your criteria.")
    # Mark everything currently present as alerted so we only chime on the NEXT new one.
    _ALERT_STATE["alerted"] |= alerts.scanner_keys(scan)
```

> Note: `_ALERT_STATE["alerted"]` accumulates all seen keys so a signal chimes
> once. On a cold start (first tick) it adds the existing signals to `alerted`
> via the union at the end — but `qualifying_new` runs BEFORE that union on the
> same tick, so the very first populated tick would alert for pre-existing
> signals. To avoid an alert-on-launch, seed `alerted` on first watcher call:
> add at the top of `_run_watcher` a one-time guard:
> `if _ALERT_STATE.get("alerted_init") is None: _ALERT_STATE["alerted"] = alerts.scanner_keys(scan); _ALERT_STATE["alerted_init"] = True; _recompute_badges(); return`

**Step 3: Manual smoke (no new unit test — wiring)**

Run the existing suite to ensure nothing broke:
Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: PASS (all existing + new tests). Browser verification happens in Task 7.

**Step 4: Commit**

```bash
git add webgui/main.py
git commit -m "feat(webgui): app-wide scanner alert watcher + badge state"
```

---

### Task 6: Badge rendering + modernized drawer

**Files:**
- Modify: `webgui/main.py` (`_nav_link`, `_layout` CSS, `_badge_refs`)

**Step 1: Add scoped drawer CSS** (call `ui.add_css(...)` once at module import, or inside `_layout` before the drawer):

```python
_NAV_CSS = """
.nav-drawer .q-item, .nav-drawer a.nav-link { border-radius: 10px; }
.nav-drawer a.nav-link { transition: background .12s ease; padding: 8px 12px; }
.nav-drawer a.nav-link:hover { background: rgba(255,255,255,.06); }
.nav-drawer a.nav-link.active { background: var(--q-primary); color: #fff; }
.nav-drawer .nav-icon { font-size: 20px; opacity: .9; }
.nav-drawer .nav-badge { margin-left: auto; }
.nav-title { font-weight: 700; letter-spacing: .02em; padding: 4px 12px 8px; opacity:.85; }
"""
```

**Step 2: Rework `_nav_link` to use the class + render a trailing badge**

```python
_badge_refs: dict = {}   # route -> badge label element (per-client; rebuilt each page)


def _nav_link(path: str, label: str, icon: str, active: str) -> None:
    classes = "nav-link w-full no-underline items-center" + (" active" if path == active else "")
    with ui.link(target=path).classes(classes):
        with ui.row().classes("items-center gap-3 w-full no-wrap"):
            ui.icon(icon).classes("nav-icon")
            ui.label(label)
            badge = ui.badge("").classes("nav-badge").props("color=red rounded")
            n = _NAV_BADGES.get(path, 0)
            badge.text = str(n) if n else ""
            badge.set_visibility(bool(n))
            _badge_refs[path] = badge
```

In `_layout`, add the `.nav-drawer` class to the drawer and reset `_badge_refs`
at the start of each build:

```python
    _badge_refs.clear()
    ui.add_css(_NAV_CSS)
    drawer = ui.left_drawer(value=True, bordered=True).classes("gap-1 nav-drawer").props("behavior=desktop")
```

Optionally add a title block at the top of the drawer:

```python
    with drawer:
        ui.label("SCHWAB TRADING").classes("nav-title")
        ...
```

**Step 3: Run the suite + verify shell still registers**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_shell.py -q`
Expected: PASS.

**Step 4: Commit**

```bash
git add webgui/main.py
git commit -m "feat(webgui): nav badges + modernized drawer styling"
```

---

### Task 7: Browser verification

**Steps:**
1. `preview_start` the `webgui` server (restart if running so code reloads).
2. Navigate to `/settings` — screenshot: confirm the Alerts + Notifications cards
   render; toggle the enable switch, change sound, click **Test sound** (should
   play; this also unlocks autoplay). Confirm settings persist (reload page →
   values retained).
3. Navigate to `/` (Scanner) and another page; confirm the drawer looks modernized
   (active pill, hover, gear Settings item) via screenshot.
4. If a fresh scan with new signals can be triggered (`Run scan`), confirm a badge
   appears on Scanner when viewing another page, and clears when you open Scanner.
   (Off-hours/no-new-signals: just confirm no errors in `preview_console_logs` and
   no spurious alert-on-launch.)
5. Report results with a screenshot.

**No commit** (verification only) unless a fix is needed.

---

## Final verification

Run the full webgui suite:
Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: all green (existing 239+ plus the new app_settings/alerts/static tests).

Then update `CLAUDE.md` (webgui structure + routes table: add `/settings`, note
the alert watcher + badges) and commit:

```bash
git commit -am "docs: record Settings page, alert watcher, and nav badges"
```
