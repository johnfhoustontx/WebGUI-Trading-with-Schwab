# Wall Display Rotation → YouTube Stream — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stream three existing dashboards (`/desk`, `/market`, `/sentiment/momentum`), rotating every 15 seconds, to a public YouTube live stream during market hours.

**Architecture:** A new raw-`HTMLResponse` route `/wall` stacks the three pages as three always-laid-out iframes and rotates them with `opacity`/`z-index`. On the prod host, Xvfb + kiosk Chrome render that one page and `ffmpeg -f x11grab` encodes it to YouTube's RTMP ingest. A systemd timer starts and stops it around the session; a wrapper script gates on `shared.market_calendar.is_trading_day()`.

**Tech Stack:** FastAPI (raw routes on NiceGUI's app), vanilla JS, Xvfb, Google Chrome (deb, not snap), ffmpeg/libx264, systemd user units.

**Design doc:** [`2026-08-31-wall-display-rotation-stream-design.md`](2026-08-31-wall-display-rotation-stream-design.md)

---

## Before you start

Read these, in this order. They are not optional context — each one contains a trap this plan deliberately steers around:

1. `webgui/desk_stream.py` — the module this one is modelled on. Same shape: a module-level `PAGE_ROUTE`, a `document()` that returns a complete HTML string, palette pulled from `pages/options/theme` rather than hand-picked.
2. `webgui/tests/test_desk_stream.py` — the test shape. Note that it tests the *emitted string*, with no browser and no Redis.
3. `deploy/systemd/generate_units.py`, specifically `_backup_units()` — the timer precedent.
4. CLAUDE.md, the "NiceGUI gotchas" block — in particular the `ui.highchart` collapse and the `vector-effect`/DOMPurify notes.

**Where this work happens.** This worktree, then dev (`:9500`), then prod via `tools/promote.sh`. Never `git pull` in the prod checkout — a hook blocks it, and the hook is right.

---

## Task 1: The `[windows.stream]` operating window

**Files:**
- Modify: `config/sessions.toml`
- Test: `shared/tests/test_market_calendar.py`

**Step 1: Write the failing test**

```python
def test_stream_window_bounds_and_membership():
    """The stream runs 08:00-15:20 CT, matching when collection runs.

    Held as its own window rather than borrowing [windows.collection]: the file
    warns against conflating windows, and widening collection must not silently
    extend a public broadcast.
    """
    from datetime import datetime
    import shared.market_calendar as mc
    mc.reset_config_cache()

    start, end = mc.window_bounds("stream")
    assert (start.hour, start.minute) == (8, 0)
    assert (end.hour, end.minute) == (15, 20)

    ct = mc.CT
    assert mc.in_window("stream", datetime(2026, 9, 2, 9, 0, tzinfo=ct)) is True
    assert mc.in_window("stream", datetime(2026, 9, 2, 7, 59, tzinfo=ct)) is False
    assert mc.in_window("stream", datetime(2026, 9, 2, 15, 21, tzinfo=ct)) is False
```

**Step 2: Run it and watch it fail**

```bash
.venv/bin/python -m pytest shared/tests/test_market_calendar.py -k stream_window -v
```

Expected: `KeyError` or a fallback-to-defaults failure — `stream` is not a known window.

**Step 3: Add the window**

In `config/sessions.toml`, after `[windows.market_snapshot]`:

```toml
[windows.stream]
# The public YouTube wall stream (/wall). Bounds match [windows.collection] on
# purpose -- the stream should be live exactly when the dashboards have moving
# data, because a stream of frozen overnight numbers is worse than no stream --
# but they are held SEPARATE, like session_flip is, so widening collection can
# never silently extend a public broadcast.
start = "08:00"
end   = "15:20"
```

Then add `"stream": {"start": "08:00", "end": "15:20"}` to `_DEFAULTS["windows"]` in `shared/market_calendar.py`, so a missing or malformed TOML degrades to the same values rather than raising. Match the surrounding style exactly.

**Step 4: Run it and watch it pass**

```bash
.venv/bin/python -m pytest shared/tests/test_market_calendar.py -k stream_window -v
```

**Step 5: Commit**

```bash
git add config/sessions.toml shared/market_calendar.py shared/tests/test_market_calendar.py
git commit -m "feat(stream): add the [windows.stream] operating window"
```

---

## Task 2: `webgui/wall.py` — the page list and skeleton

**Files:**
- Create: `webgui/wall.py`
- Create: `webgui/tests/test_wall.py`

**Step 1: Write the failing test**

```python
import wall


def test_pages_are_the_three_dashboards_in_rotation_order():
    assert [p["path"] for p in wall.PAGES] == [
        "/desk", "/market", "/sentiment/momentum"]
    # Every panel needs a human label for the overlay -- a viewer landing
    # mid-rotation has no other way to know what they are looking at.
    assert all(p["label"] for p in wall.PAGES)


def test_route_is_a_constant_not_a_literal():
    assert wall.PAGE_ROUTE == "/wall"


def test_document_is_a_complete_standalone_html_page():
    doc = wall.document()
    assert doc.startswith("<!DOCTYPE html>")
    assert "</html>" in doc
    # It carries its OWN style block: it is a raw HTMLResponse, the documented
    # out-of-scope case, not a NiceGUI page.
    assert "<style>" in doc


def test_document_embeds_all_three_pages_as_iframes():
    doc = wall.document()
    for page in wall.PAGES:
        assert f'src="{page["path"]}"' in doc
    assert doc.count("<iframe") == 3
```

**Step 2: Run and watch it fail**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_wall.py -v
```

Expected: `ModuleNotFoundError: No module named 'wall'`.

**Step 3: Minimal implementation**

Create `webgui/wall.py` with a module docstring explaining *why* it exists (follow `desk_stream.py`'s tone — it explains the trade it is making, not just what it does), then:

```python
PAGE_ROUTE = "/wall"

# Rotation order, and the single source of it. The overlay label, the iframe
# list and the JS rotation all read this, so adding a fourth screen is one entry
# here and nothing else.
PAGES = [
    {"path": "/desk", "label": "DESK"},
    {"path": "/market", "label": "MACRO BOARD"},
    {"path": "/sentiment/momentum", "label": "MOMENTUM"},
]

DWELL_MS = 15_000   # time each page holds the screen
FADE_MS = 400       # crossfade duration; see the design doc on why not a cut
```

Then `document()` returning a complete page with the three iframes. Keep it minimal — rotation and overlay arrive in Tasks 3 and 4.

**Step 4: Run and watch it pass**

**Step 5: Commit**

```bash
git add webgui/wall.py webgui/tests/test_wall.py
git commit -m "feat(wall): standalone /wall document embedding the three dashboards"
```

---

## Task 3: Rotation by opacity — never `display:none`

**This is the load-bearing task.** Everything else is plumbing.

**Files:**
- Modify: `webgui/wall.py`
- Modify: `webgui/tests/test_wall.py`

**Step 1: Write the failing tests**

```python
import re


def test_panels_are_never_hidden_with_display_none_or_visibility():
    """An iframe hidden with display:none has ZERO layout size, and this app's
    Highcharts have no ResizeObserver -- a chart that mounts at 0x0 renders
    collapsed forever. None of these three pages draws a chart TODAY, but the
    day one does (or a chart-heavy page like /options/gamma is swapped in) the
    panel would silently break. Rotation is opacity + z-index, and this test is
    what keeps it that way.
    """
    doc = wall.document()
    panel_css = re.search(r"\.panel\s*\{[^}]*\}", doc, re.S).group(0)
    assert "display:none" not in panel_css.replace(" ", "")
    assert "visibility" not in panel_css
    assert "opacity" in panel_css


def test_every_panel_is_laid_out_at_full_size():
    doc = wall.document()
    panel_css = re.search(r"\.panel\s*\{[^}]*\}", doc, re.S).group(0)
    squashed = panel_css.replace(" ", "")
    assert "position:absolute" in squashed
    assert "width:100%" in squashed and "height:100%" in squashed


def test_rotation_timings_come_from_the_constants():
    doc = wall.document()
    assert str(wall.DWELL_MS) in doc
    assert str(wall.FADE_MS) in doc


def test_fade_is_shorter_than_the_dwell():
    # A fade longer than the dwell would mean never settling on a page.
    assert wall.FADE_MS < wall.DWELL_MS
```

**Step 2: Run and watch them fail**

**Step 3: Implement**

CSS — note there is no `display` or `visibility` anywhere in `.panel`:

```css
.panel {
  position: absolute; top: 0; left: 0;
  width: 100%; height: 100%;
  border: 0;
  opacity: 0;
  transition: opacity {fade}ms ease-in-out;
}
.panel.on { opacity: 1; }
```

JS:

```javascript
const PANELS = Array.from(document.querySelectorAll('.panel'));
const LABELS = {labels};
let idx = 0;

function show(n) {
  PANELS.forEach((p, i) => p.classList.toggle('on', i === n));
  document.getElementById('page-label').textContent = LABELS[n];
}

show(0);
setInterval(() => { idx = (idx + 1) % PANELS.length; show(idx); }, {dwell});
```

`z-index` is not strictly needed once all panels are stacked and only one is opaque, but set `.panel.on { z-index: 1 }` so the visible panel is also the one that receives any hit-testing — it costs one line and removes a whole class of "why did the wrong page get the click" confusion if anyone ever drives this interactively.

**Step 4: Run the full wall suite**

```bash
cd webgui && ../.venv/bin/python -m pytest tests/test_wall.py -v
```

**Step 5: Commit**

```bash
git add webgui/wall.py webgui/tests/test_wall.py
git commit -m "feat(wall): rotate panels by opacity, never display:none"
```

---

## Task 4: The branding and clock overlay

**Files:**
- Modify: `webgui/wall.py`
- Modify: `webgui/tests/test_wall.py`

**Step 1: Write the failing tests**

```python
def test_overlay_uses_the_apps_own_brand_not_a_copy():
    """A second hand-written wordmark would drift from config/theme.toml.
    The stream must render whatever the app header renders."""
    import main
    from pages.options import theme
    doc = wall.document()
    assert theme.BRAND_NAME_A in doc and theme.BRAND_NAME_B in doc
    assert main.brand_lockup_html(mark=False) in doc


def test_overlay_carries_a_clock_and_a_page_label():
    doc = wall.document()
    assert 'id="clock"' in doc
    assert 'id="page-label"' in doc


def test_disclaimer_slot_exists_and_is_empty_by_decision():
    """Left empty by operator decision (design doc s.2). Kept as a single
    constant so turning it on is a one-line change, not a redesign."""
    assert wall.DISCLAIMER == ""
    assert 'id="disclaimer"' in wall.document()


def test_clock_is_central_time():
    # The trading clock. A stream stamped in UTC is a stream nobody can use.
    assert "America/Chicago" in wall.document()
```

**Step 2: Run and watch them fail**

**Step 3: Implement**

Add `DISCLAIMER = ""` as a module constant with a comment recording that it is deliberately empty and what it is for. Pull `theme.BRAND_CSS` and `theme.BRAND_FONT_HEAD_HTML` into the `<head>`, and `main.brand_lockup_html(mark=False)` into the overlay bar.

⚠ Importing `main` from `wall` risks a circular import, since `main` will import `wall` in Task 5. Import it lazily **inside** `document()`, the way `main._serve_manual` imports `pages.manuals` inside the handler. If that proves awkward, move `brand_lockup_html` usage to a small local helper instead — but do not create a second wordmark.

Clock JS:

```javascript
function tick() {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('en-US', {
      timeZone: 'America/Chicago', hour12: false });
}
tick(); setInterval(tick, 1000);
```

**Step 4: Run and watch them pass**

**Step 5: Commit**

```bash
git add webgui/wall.py webgui/tests/test_wall.py
git commit -m "feat(wall): brand, clock and page-label overlay"
```

---

## Task 5: Register the route

**Files:**
- Modify: `webgui/main.py` (immediately after the `/desk/stream` handler, ~line 333)
- Modify: `webgui/tests/test_wall.py`

**Step 1: Write the failing test**

```python
def test_route_is_registered_as_a_raw_html_response():
    import main
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert wall.PAGE_ROUTE in paths


def test_wall_is_not_a_nav_page():
    """A display target, not a destination. It must not appear in the rail, the
    tab strips or the breadcrumb registry -- and specifically must not become a
    third entry in test_shell's _LANDING_ROUTES exemption."""
    import main
    assert wall.PAGE_ROUTE not in main._NAV_LABEL
    assert wall.PAGE_ROUTE not in main.EXTERNAL_RAIL_ROUTES
```

**Step 2: Run and watch it fail**

**Step 3: Implement**

In `webgui/main.py`, add `import wall  # noqa: E402` beside `import desk_stream`, then:

```python
# ── The wall display (/wall) ─────────────────────────────────────────────────
# A raw route, for the same reason /desk/live is one: the capture browser wants
# a document, not a NiceGUI client. It is NOT in NAV -- it is a display target
# rendered by the streaming pipeline, not a page a reader navigates to.
@app.get(wall.PAGE_ROUTE)
def _serve_wall():
    """The rotating wall document — self-contained, so its own <style> applies."""
    return HTMLResponse(wall.document())
```

**Step 4: Run the whole webgui suite**

```bash
cd webgui && ../.venv/bin/python -m pytest -q
```

Compare the **failing set**, not the count — `-rf` is already the default. Baseline before this change is what you diff against.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_wall.py
git commit -m "feat(wall): register /wall as a raw HTMLResponse route"
```

---

## Task 6: The stream runner script

**Files:**
- Create: `tools/stream_wall.sh`
- Create: `tools/tests/test_stream_wall.py`

**Step 1: Write the failing test**

The script is shell, so test it at source level — the same technique `test_proxy.py` uses to guard the Tier-1 import rule:

```python
import pathlib
import re

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "tools" / "stream_wall.sh"


def test_gates_on_the_market_calendar_not_a_holiday_literal():
    """systemd's timer knows Mon..Fri; it does not know Thanksgiving. The repo
    rule is that no new holiday literal goes anywhere but market_calendar."""
    text = SCRIPT.read_text()
    assert "is_trading_day" in text
    assert not re.search(r"\b(thanksgiving|christmas|juneteenth)\b", text, re.I)


def test_never_embeds_the_stream_key():
    """The key lives in an operator-created 0600 EnvironmentFile and reaches the
    process as $RTMP_URL. A key in the repo is a leaked key."""
    text = SCRIPT.read_text()
    assert "RTMP_URL" in text
    assert "rtmp://" not in text.replace('"$RTMP_URL"', "")


def test_pins_a_two_second_keyframe_interval():
    """YouTube caps the keyframe interval at 4s and recommends 2s. At 30fps
    that is -g 60, and -sc_threshold 0 keeps it strict."""
    text = SCRIPT.read_text()
    assert "-g 60" in text and "-sc_threshold 0" in text


def test_sends_a_silent_audio_track():
    """YouTube ingest is unreliable with video-only. anullsrc costs nothing."""
    assert "anullsrc" in SCRIPT.read_text()


def test_cleans_up_its_children_on_exit():
    """Xvfb and Chrome outliving a stopped unit would hold the display and make
    the next start fail with a bind error nobody reads."""
    text = SCRIPT.read_text()
    assert "trap" in text and "set -euo pipefail" in text
```

**Step 2: Run and watch it fail**

```bash
.venv/bin/python -m pytest tools/tests/test_stream_wall.py -v
```

**Step 3: Implement**

`tools/stream_wall.sh` — `set -euo pipefail`, a `trap ... EXIT` that kills Xvfb and Chrome, the trading-day gate (exit 0, not 1 — a holiday is a normal outcome, and a non-zero exit would trip `Restart=on-failure` into a storm), then Xvfb → Chrome → ffmpeg in the foreground so systemd tracks the encoder as the main process.

```bash
"$PYTHON" -c 'import datetime,sys; sys.path.insert(0,".");
from shared.market_calendar import is_trading_day;
sys.exit(0 if is_trading_day(datetime.date.today()) else 42)' || {
  echo "not a trading day - standing down"; exit 0; }
```

Use the exact ffmpeg invocation from the design doc.

⚠ Write the file with **LF line endings**. A CRLF shell script fails with `bad interpreter: /bin/bash^M`, and `.gitattributes` is what normally prevents this — verify it covers `*.sh`.

**Step 4: Run and watch it pass**

**Step 5: Commit**

```bash
git add tools/stream_wall.sh tools/tests/test_stream_wall.py
git commit -m "feat(stream): market-hours-gated wall capture and encode script"
```

---

## Task 7: The systemd unit and timer

**Files:**
- Modify: `deploy/systemd/generate_units.py`
- Modify: `tests/test_systemd_units.py`

**Step 1: Write the failing tests**

```python
def test_stream_units_are_generated():
    units = generate_units.render_all()
    assert f"trading-{generate_units.ENV_NAME}-stream.service" in units
    assert f"trading-{generate_units.ENV_NAME}-stream.timer" in units


def test_storm_cap_is_in_the_unit_section_not_the_service_section():
    """systemd moved StartLimit* to [Unit] in v229 and SILENTLY IGNORES them in
    [Service] -- the cap would look configured and not exist."""
    svc = generate_units.render_all()[
        f"trading-{generate_units.ENV_NAME}-stream.service"]
    unit_block = svc.split("[Service]")[0]
    assert "StartLimitBurst=" in unit_block
    assert "StartLimitBurst=" not in svc.split("[Service]")[1]


def test_stream_stops_with_the_stack():
    """Unlike the backup job, this one MUST be PartOf the target: it renders the
    webgui, so if the stack is down it would broadcast an error page."""
    svc = generate_units.render_all()[
        f"trading-{generate_units.ENV_NAME}-stream.service"]
    assert f"PartOf={generate_units.target_name()}" in svc


def test_timer_starts_and_stops_around_the_stream_window():
    from shared import market_calendar as mc
    start, end = mc.window_bounds("stream")
    tmr = generate_units.render_all()[
        f"trading-{generate_units.ENV_NAME}-stream.timer"]
    assert f"{start.hour:02d}:{start.minute:02d}:00" in tmr
```

**Step 2: Run and watch them fail**

**Step 3: Implement**

Add `_stream_units()` modelled on `_backup_units()`, and call it from `render_all()`. Read the window from `market_calendar.window_bounds("stream")` so the unit and the config cannot disagree — do not retype `08:00`.

Two things that differ from the backup job, and the docstring should say why:
- **`PartOf` the target** (backup is deliberately not) — see the test above.
- **Holidays ARE filtered**, in the wrapper. The backup unit's comment explains why *it* does not filter them: a redundant holiday backup wastes a slot, a missed session loses data, so the asymmetry favours running. For a public broadcast the asymmetry is the other way — a holiday stream shows frozen numbers to an audience.

Stopping needs a second timer unit (`OnCalendar` at the window end running `systemctl --user stop`), or `RuntimeMaxSec` computed from the window length. Prefer **`RuntimeMaxSec`**: one unit, and it cannot leave the stream running because a stop timer failed to fire.

**Step 4: Run and watch them pass**

```bash
.venv/bin/python -m pytest tests/test_systemd_units.py -v
```

**Step 5: Commit**

```bash
git add deploy/systemd/generate_units.py tests/test_systemd_units.py
git commit -m "feat(stream): generate the wall-stream service and timer"
```

---

## Task 8: Host provisioning (dev first)

Not a TDD task — this changes the machine, not the repo. Run it against **dev**.

**Step 1: Install the packages**

```bash
ssh vps2 'sudo apt-get update && sudo apt-get install -y xvfb ffmpeg fonts-liberation fonts-noto-color-emoji fonts-noto-cjk'
```

**Step 2: Install Google Chrome from Google's repo — not the snap**

```bash
ssh vps2 'wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && sudo apt-get install -y /tmp/chrome.deb && google-chrome --version'
```

**Step 3: Confirm Chrome starts sandboxed**

Ubuntu 24.04's AppArmor restriction on unprivileged user namespaces breaks Chrome's sandbox. Verify before reaching for `--no-sandbox`:

```bash
ssh vps2 'Xvfb :99 -screen 0 1920x1080x24 & sleep 2; DISPLAY=:99 google-chrome --headless=new --dump-dom http://127.0.0.1:9500/wall | head -20'
```

If it fails on the sandbox, install the AppArmor profile Chrome's deb ships rather than disabling the sandbox — this browser loads only localhost, but "only localhost" is not a reason to run a browser unsandboxed on a box holding Schwab credentials.

**Step 4: Screenshot `/wall` and actually look at it**

```bash
ssh vps2 'DISPLAY=:99 google-chrome --headless=new --screenshot=/tmp/wall.png --window-size=1920,1080 http://127.0.0.1:9500/wall'
scp vps2:/tmp/wall.png .
```

**Check for tofu boxes and literal ligature text.** If Google Fonts is slow or blocked, Quasar's Material Icons render as the word `trending_up` where an icon belongs. This is invisible to every test in the repo and highly visible on a public stream.

---

## Task 9: Verify in dev, end to end

1. `git push`, then in the **dev** checkout fast-forward and restart the webgui unit.
2. Load `http://127.0.0.1:9500/wall` through the SSH tunnel and watch a full rotation — all three panels, two crossfades, clock ticking, labels correct.
3. Run `tools/stream_wall.sh` by hand against a **private or unlisted** YouTube stream first. Confirm the rotation survives on the far side, not just locally.
4. Measure CPU **during market hours**, browser and encoder separately:
   ```bash
   ssh vps2 'top -b -n1 | grep -E "chrome|ffmpeg|Xvfb"; cat /proc/loadavg'
   ```
   With no swap on this box, watch RSS as closely as CPU.

⚠ "Tests pass" is not "verified in dev". Nearly all of this is runtime surface.

---

## Task 10: Operator steps (you, not Claude)

**These are yours because they involve a credential I will not handle.**

1. In YouTube Studio, create the live stream and copy its stream key.
2. On the prod host:
   ```bash
   sudo install -d -m 700 /etc/neuralstrike-stream
   sudo install -m 600 /dev/null /etc/neuralstrike-stream/env
   sudo nano /etc/neuralstrike-stream/env
   ```
   One line: `RTMP_URL="rtmp://a.rtmp.youtube.com/live2/YOUR-KEY-HERE"`
3. Promote and install the units:
   ```bash
   tools/promote.sh
   ```
   ```bash
   .venv/bin/python -m deploy.systemd.generate_units --install && systemctl --user daemon-reload && systemctl --user enable --now trading-prod-stream.timer
   ```
4. Verify: `systemctl --user list-timers 'trading-prod-stream*'`

---

## Definition of done

- [ ] `/wall` renders all three panels and rotates cleanly, verified in a browser
- [ ] Rotation uses opacity only — the `display:none` guard test passes
- [ ] Full webgui suite: failing **set** unchanged from baseline
- [ ] `tools/tests/test_stream_wall.py` and `tests/test_systemd_units.py` pass
- [ ] Screenshot reviewed — no tofu, no ligature text
- [ ] CPU and RSS measured during market hours, recorded in the design doc
- [ ] Stream verified on YouTube through a full rotation cycle
- [ ] `docs/CHANGELOG.md` entry added
- [ ] CLAUDE.md route table gains `/wall`
