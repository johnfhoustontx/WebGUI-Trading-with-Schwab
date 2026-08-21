# Desk Spoken Alerts + Neon Row Highlight — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When a new flow alert or a newly-opened position appears on `/desk`, speak the ticker and the cause in a natural human voice, and glow the row for 10 seconds.

**Architecture:** A new Tier-1 module `webgui/voice.py` turns a phrase into a cached mp3 under `webgui/data/voice/` (served at `/voice`) using `edge-tts`; `webgui/pages/desk.py` gains pure new-row/changed-flag detection, a ten-class CSS neon animation that *resumes* across the panel's wholesale repaints, and a small JS play queue on its own `<audio>` element.

**Tech Stack:** Python 3.11, NiceGUI, `edge-tts` (Microsoft neural voices, no API key), pytest.

**Design doc:** [`2026-08-21-desk-voice-alerts-design.md`](2026-08-21-desk-voice-alerts-design.md) — read it before starting.

---

## Conventions for every task

**This is a git worktree — the venv is at the repo root.** Every test command is:

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/inspiring-sinoussi-b1cc78/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest . -q)
```

Confine the `cd` to a subshell as shown. Commit after every task.

**The webgui baseline is 2320 green** (2026-08-20). Compare the failing *set*, never the count.

**Tailwind-first is mandatory.** No `.style(...)`, no inline `style=`. The only new CSS goes through `desk.py`'s existing `ui.add_css` escape hatch. `webgui/tests/test_no_inline_style.py` already covers `desk.py` and must stay green.

---

## Task 1: Dependency and cache directory

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`

**Step 1: Add the dependency**

Append to `requirements.txt`, in the same commented style as its neighbours:

```
edge-tts>=7.0                # spoken Desk alerts: Microsoft neural TTS, no API key.
                             # Pulls only aiohttp (already required) + tabulate.
                             # webgui/voice.py guards the import — absent it, the
                             # Desk simply does not speak.
```

**Step 2: Ignore the generated clip cache**

`webgui/data/` is only *partially* ignored today (`webgui/data/eod/`, `webgui/data/settings.json`). Add beside those:

```
webgui/data/voice/
```

**Step 3: Install into the venv**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pip install "edge-tts>=7.0"
```

Expected: `Successfully installed edge-tts-… tabulate-…`

**Step 4: Verify it synthesizes**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -c "import asyncio, edge_tts; asyncio.run(edge_tts.Communicate('S P Y. Crossover alert, calls over.', 'en-US-AriaNeural', rate='+8%').save('probe.mp3'))"
```

Expected: a `probe.mp3` of roughly 25-30 KB. Delete it afterwards.

**Step 5: Commit**

```bash
git add requirements.txt .gitignore
git commit -m "build: add edge-tts for Desk spoken alerts"
```

---

## Task 2: `voice.py` — the pure speech builders

**Files:**
- Create: `webgui/voice.py`
- Test: `webgui/tests/test_voice.py`

**Step 1: Write the failing tests**

Create `webgui/tests/test_voice.py`:

```python
"""Pure speech builders for the Desk's spoken alerts.

No network anywhere in this file: synthesis is monkeypatched in the cache
tests below, and the builders here touch nothing but strings.
"""
import voice


# ── spell ────────────────────────────────────────────────────────────────────
def test_spell_separates_the_letters_of_a_ticker():
    assert voice.spell("SPY") == "S P Y"


def test_spell_drops_the_index_dollar_sign():
    # "$SPX" spoken as "dollar S P X" would be wrong, and as "spux" worse.
    assert voice.spell("$SPX") == "S P X"


def test_spell_upcases_and_keeps_digits():
    assert voice.spell("brk.b") == "B R K B"


def test_spell_of_nothing_is_empty_not_a_crash():
    assert voice.spell(None) == ""
    assert voice.spell("") == ""


# ── more_tail ────────────────────────────────────────────────────────────────
def test_more_tail_is_silent_when_nothing_else_arrived():
    assert voice.more_tail(0) == ""
    assert voice.more_tail(None) == ""
    assert voice.more_tail(-3) == ""


def test_more_tail_counts_the_rest():
    assert voice.more_tail(5) == "Plus 5 more."


def test_more_tail_reads_naturally_at_one():
    # Deliberately not "1 more alerts" — and deliberately not the word "alert"
    # at all, since positions use this same tail.
    assert voice.more_tail(1) == "Plus 1 more."
```

**Step 2: Run to verify it fails**

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/inspiring-sinoussi-b1cc78/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_voice.py -q)
```

Expected: `ModuleNotFoundError: No module named 'voice'`

**Step 3: Write the minimal implementation**

Create `webgui/voice.py`:

```python
"""Spoken alert clips for the Desk — a phrase to mp3 cache over edge-tts.

**Tier note.** This module imports ``edge_tts``, which is neither an engine nor
a Schwab caller. It is a presentation concern — the audio equivalent of the
bundled WAVs already in ``webgui/static/sounds/`` — so it does not breach the
documented Tier-1 rule that the webgui imports only ``nicegui`` +
``shared.bus`` + ``shared.contracts``. Do not read it as a violation.

**Nothing here raises.** A missing package, a dead network or an unwritable
cache directory all degrade to ``None``, which the caller reads as "no speech
this tick" — the row still glows and the existing chime is untouched. That
matters because the alternative to silence is a traceback on the landing page.

The builders at the top are pure string work and carry the whole of the spoken
vocabulary; the cache layer below is the only part that touches the network.
"""

# The six en-US neural voices offered in Settings. Aria is the default because
# it was the one picked from a live listening test of all six, not because it
# is first alphabetically.
DEFAULT_VOICE = "en-US-AriaNeural"
VOICES = ("en-US-AriaNeural", "en-US-AndrewNeural", "en-US-AvaNeural",
          "en-US-EmmaNeural", "en-US-BrianNeural", "en-US-ChristopherNeural")

# A touch faster than default. An announcement competing with a moving tape
# wants to be over quickly; past about +15% the spelled tickers start to slur.
RATE = "+8%"


def spell(symbol):
    """``'$SPX'`` -> ``'S P X'``. Tickers are ALWAYS spelled, squawk-style.

    Spelling is not a stylistic choice. "SPY" read as a word is "spy", which is
    actively misleading, and no word-vs-letters rule can be right for an
    unbounded symbol set. Non-alphanumerics (the index ``$``, a class-share
    dot) are dropped rather than spoken.
    """
    return " ".join(c for c in str(symbol or "").upper() if c.isalnum())


def more_tail(n):
    """``'Plus 5 more.'`` — ``''`` when nothing else arrived.

    Deliberately says "more" and not "more alerts": positions share this tail,
    and one word that is right for both beats two that drift apart.
    """
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    return f"Plus {n} more." if n > 0 else ""
```

**Step 4: Run to verify it passes**

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/inspiring-sinoussi-b1cc78/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_voice.py -q)
```

Expected: `10 passed`

**Step 5: Commit**

```bash
git add webgui/voice.py webgui/tests/test_voice.py
git commit -m "feat(voice): ticker spelling and burst-count tail"
```

---

## Task 3: `voice.py` — the phrase builders

**Files:**
- Modify: `webgui/voice.py`
- Modify: `webgui/tests/test_voice.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_voice.py`:

```python
# ── flow_phrase ──────────────────────────────────────────────────────────────
# The rows below are the shape ``pages.options.flow.alert_rows`` publishes:
# ``kind`` and ``side`` are already the DISPLAY labels, not the raw keys.
def test_flow_phrase_names_the_ticker_then_the_cause():
    row = {"symbol": "SPY", "kind": "Crossover", "side": "Calls over"}
    assert voice.flow_phrase(row) == "S P Y. Crossover alert, calls over."


def test_flow_phrase_covers_all_four_alert_kinds():
    cases = {
        ("NDX", "Unusual activity", "Put"): "N D X. Unusual activity alert, put.",
        ("QQQ", "Gamma flip", "To negative"): "Q Q Q. Gamma flip alert, to negative.",
        ("AMD", "Big delta", "Call"): "A M D. Big delta alert, call.",
        ("SPY", "Crossover", "Puts over"): "S P Y. Crossover alert, puts over.",
    }
    for (sym, kind, side), want in cases.items():
        assert voice.flow_phrase(
            {"symbol": sym, "kind": kind, "side": side}) == want


def test_flow_phrase_omits_a_missing_side_without_a_dangling_comma():
    row = {"symbol": "SPY", "kind": "Crossover", "side": ""}
    assert voice.flow_phrase(row) == "S P Y. Crossover alert."


def test_flow_phrase_folds_the_burst_count_into_the_same_sentence():
    row = {"symbol": "SPY", "kind": "Crossover", "side": "Calls over"}
    assert voice.flow_phrase(row, extra=5) == \
        "S P Y. Crossover alert, calls over. Plus 5 more."


def test_flow_phrase_survives_a_junk_row():
    # Total over a malformed row, like every other builder the Desk reads.
    assert voice.flow_phrase(None) == "Flow alert."
    assert voice.flow_phrase({}) == "Flow alert."


# ── position_phrase ──────────────────────────────────────────────────────────
def test_position_phrase_names_the_ticker_and_the_strategy():
    row = {"symbol": "SPY", "strategy": "put_credit_spread"}
    assert voice.position_phrase(row) == "S P Y. New position, put credit spread."


def test_position_phrase_without_a_strategy_still_announces_the_position():
    assert voice.position_phrase({"symbol": "QQQ"}) == "Q Q Q. New position."


def test_position_phrase_takes_the_burst_tail_too():
    row = {"symbol": "SPY", "strategy": "iron_condor"}
    assert voice.position_phrase(row, extra=2) == \
        "S P Y. New position, iron condor. Plus 2 more."
```

**Step 2: Run to verify it fails**

Expected: `AttributeError: module 'voice' has no attribute 'flow_phrase'`

**Step 3: Write the implementation**

Append to `webgui/voice.py`:

```python
def _sentence(symbol, body, extra):
    """``'S P Y. <body>.'`` plus the burst tail. The shared shape of both phrases."""
    parts = []
    sym = spell(symbol)
    if sym:
        parts.append(f"{sym}.")
    parts.append(f"{body}.")
    tail = more_tail(extra)
    if tail:
        parts.append(tail)
    return " ".join(parts)


def flow_phrase(row, extra=0):
    """``'S P Y. Crossover alert, calls over.'``

    ``kind`` and ``side`` are read straight off the row that
    ``pages.options.flow.alert_rows`` built, so the spoken words are the SAME
    words the panel prints. That is not tidiness — the Desk's governing rule is
    that it composes and never re-derives, and a spoken vocabulary drifting
    from the printed one would be the documented sectors-vs-rotation bug in a
    new place.
    """
    d = row if isinstance(row, dict) else {}
    kind = str(d.get("kind") or "Flow").strip() or "Flow"
    side = str(d.get("side") or "").strip()
    body = f"{kind} alert" + (f", {side.lower()}" if side else "")
    return _sentence(d.get("symbol"), body, extra)


def position_phrase(row, extra=0):
    """``'S P Y. New position, put credit spread.'``

    Only ever spoken for a position that is genuinely NEW to the book. A flag
    change (OK -> AT RISK -> RESCUE) glows but stays silent, by decision — see
    the design doc.
    """
    d = row if isinstance(row, dict) else {}
    strat = str(d.get("strategy") or "").replace("_", " ").strip().lower()
    body = f"New position, {strat}" if strat else "New position"
    return _sentence(d.get("symbol"), body, extra)
```

**Step 4: Run to verify it passes**

Expected: `18 passed`

**Step 5: Commit**

```bash
git add webgui/voice.py webgui/tests/test_voice.py
git commit -m "feat(voice): flow and position announcement phrases"
```

---

## Task 4: `voice.py` — the mp3 cache

**Files:**
- Modify: `webgui/voice.py`
- Modify: `webgui/tests/test_voice.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_voice.py`:

```python
import pathlib


# ── cache keys ───────────────────────────────────────────────────────────────
def test_clip_name_is_stable_for_the_same_phrase():
    a = voice.clip_name("S P Y. Crossover alert.")
    assert a == voice.clip_name("S P Y. Crossover alert.")
    assert a.endswith(".mp3")


def test_clip_name_changes_with_the_voice():
    # The voice is part of the key, or switching voices in Settings would keep
    # serving clips spoken by the previous one.
    a = voice.clip_name("hello", voice_name="en-US-AriaNeural")
    b = voice.clip_name("hello", voice_name="en-US-BrianNeural")
    assert a != b


def test_clip_url_is_served_from_the_voice_mount():
    url = voice.clip_url("hello")
    assert url.startswith("/voice/") and url.endswith(".mp3")


# ── ensure ───────────────────────────────────────────────────────────────────
def test_ensure_synthesizes_once_then_serves_the_cached_file(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    calls = []

    def _fake(text, voice_name, rate, dest):
        calls.append(text)
        dest.write_bytes(b"ID3fake")

    monkeypatch.setattr(voice, "_synthesize", _fake)

    first = voice.ensure("S P Y. Crossover alert.")
    second = voice.ensure("S P Y. Crossover alert.")
    assert first == second and first.startswith("/voice/")
    assert calls == ["S P Y. Crossover alert."]      # the second call is a hit


def test_ensure_returns_none_when_synthesis_fails(tmp_path, monkeypatch):
    # No internet, no edge_tts, unwritable dir — all land here, and all must
    # degrade to silence rather than a traceback on the landing page.
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    voice.reset_warning()
    assert voice.ensure("anything") is None


def test_ensure_ignores_an_empty_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    assert voice.ensure("") is None
    assert voice.ensure(None) is None


def test_ensure_does_not_serve_a_zero_byte_clip(tmp_path, monkeypatch):
    # A crashed or interrupted synthesis can leave an empty file. Serving it
    # would be a silent permanent failure for that one phrase.
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    dest = tmp_path / voice.clip_name("hello")
    dest.write_bytes(b"")
    monkeypatch.setattr(voice, "_synthesize",
                        lambda t, v, r, d: d.write_bytes(b"ID3real"))
    assert voice.ensure("hello") is not None
    assert dest.read_bytes() == b"ID3real"


# ── prewarm ──────────────────────────────────────────────────────────────────
def test_prewarm_texts_covers_every_symbol_and_cause():
    texts = voice.prewarm_texts(["SPY", "QQQ"])
    assert len(texts) == 2 * len(voice.FLOW_CAUSES)
    assert "S P Y. Crossover alert, calls over." in texts
    assert "Q Q Q. Big delta alert, put." in texts


def test_prewarm_texts_of_nothing_is_empty():
    assert voice.prewarm_texts(None) == []
    assert voice.prewarm_texts([]) == []
```

**Step 2: Run to verify it fails**

Expected: `AttributeError: module 'voice' has no attribute 'clip_name'`

**Step 3: Write the implementation**

Append to `webgui/voice.py`. Add the stdlib imports at the TOP of the file (above the constants):

```python
import hashlib
import logging
import os
import pathlib
import threading
```

Then append:

```python
# Generated clips, gitignored and regenerating on demand. They live under
# ``data/`` rather than ``static/`` precisely because they are generated: a
# build artefact in a committed asset directory is a diff nobody wants.
CACHE_DIR = pathlib.Path(__file__).resolve().parent / "data" / "voice"
URL_PREFIX = "/voice"

# Warn ONCE per process, not once per tick. A dead network on a page that polls
# every two seconds would otherwise write 43,200 identical tracebacks a day.
_WARNED = {"done": False}


def reset_warning():
    """Re-arm the one-shot failure warning (test helper)."""
    _WARNED["done"] = False


def clip_name(text, voice_name=None, rate=RATE):
    """The cache filename for a phrase.

    The VOICE and the RATE are part of the key, not just the text — otherwise
    switching voices in Settings would keep serving clips spoken by the old one,
    with no way to tell from the filename.
    """
    key = f"{voice_name or DEFAULT_VOICE}|{rate}|{text}".encode("utf-8")
    return hashlib.sha1(key).hexdigest() + ".mp3"


def clip_url(text, voice_name=None, rate=RATE):
    """The URL the browser fetches the clip from (see main.py's /voice mount)."""
    return f"{URL_PREFIX}/{clip_name(text, voice_name, rate)}"


def _synthesize(text, voice_name, rate, dest):
    """Write ONE mp3 to ``dest``. Raises on any failure — ``ensure`` degrades.

    Writes to a per-process, per-thread temp name and then renames, so a reader
    can never be handed a half-written clip and two concurrent synthesis calls
    for the same phrase cannot corrupt each other's output.
    """
    import asyncio

    import edge_tts

    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        asyncio.run(edge_tts.Communicate(text, voice_name, rate=rate).save(str(tmp)))
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _usable(path):
    """A cached clip that is actually playable — present AND non-empty.

    The size check is not paranoia: an interrupted synthesis leaves a zero-byte
    file, and serving it would make that ONE phrase permanently silent with
    nothing in the logs to say why.
    """
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def ensure(text, voice_name=None, rate=RATE):
    """Local URL for the phrase's clip, synthesizing on a miss. ``None`` on failure.

    BLOCKING (~600 ms on a miss, measured) — callers on the event loop must go
    through ``nicegui.run.io_bound``. A cache hit is a single ``stat``.
    """
    text = str(text or "").strip()
    if not text:
        return None
    voice_name = voice_name or DEFAULT_VOICE
    dest = CACHE_DIR / clip_name(text, voice_name, rate)
    if _usable(dest):
        return clip_url(text, voice_name, rate)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _synthesize(text, voice_name, rate, dest)
        if not _usable(dest):
            raise OSError("synthesis produced no audio")
        return clip_url(text, voice_name, rate)
    except Exception:  # noqa: BLE001 — every failure mode is the same: silence.
        if not _WARNED["done"]:
            _WARNED["done"] = True
            logging.getLogger("webgui").warning(
                "voice synthesis unavailable — Desk spoken alerts are off",
                exc_info=True)
        return None


# The (kind, side) pairs the flow panel can produce, as DISPLAY labels — the
# same eight ``pages.options.flow`` maps its four types and their sides onto.
# Restated here rather than imported because ``voice`` must stay importable
# with no ``pages`` package on the path (the prewarm runs before any page).
FLOW_CAUSES = (("Crossover", "Calls over"), ("Crossover", "Puts over"),
               ("Unusual activity", "Call"), ("Unusual activity", "Put"),
               ("Gamma flip", "To positive"), ("Gamma flip", "To negative"),
               ("Big delta", "Call"), ("Big delta", "Put"))


def prewarm_texts(symbols):
    """Every flow phrase the given symbols can produce — the prewarm work list.

    Flow only. A new position is a thing the user just did, so they are already
    looking at the screen and a 600 ms first synthesis costs nothing; a flow
    alert arrives unbidden and is the case worth paying disk for.
    """
    out = []
    for sym in symbols or ():
        for kind, side in FLOW_CAUSES:
            out.append(flow_phrase({"symbol": sym, "kind": kind, "side": side}))
    return out


def prewarm(symbols, voice_name=None):
    """Synthesize the flow phrase set in the background. Never raises.

    Fire-and-forget on a daemon thread: failure to prewarm is not an error, it
    just leaves the lazy path to pay the 600 ms on first use.
    """
    texts = prewarm_texts(symbols)
    if not texts:
        return None

    def _run():
        for text in texts:
            if ensure(text, voice_name) is None:
                return          # first failure means the whole set will fail

    t = threading.Thread(target=_run, name="voice-prewarm", daemon=True)
    t.start()
    return t
```

**Step 4: Run to verify it passes**

Expected: `28 passed`

**Step 5: Commit**

```bash
git add webgui/voice.py webgui/tests/test_voice.py
git commit -m "feat(voice): permanent on-disk clip cache with background prewarm"
```

---

## Task 5: Serve the cache at `/voice`

**Files:**
- Modify: `webgui/main.py:46-49`

**Step 1: Mount the directory**

Immediately after the existing `/static` mount:

```python
# Serve bundled static assets (alert sounds) at /static.
_STATIC_DIR = _REPO_ROOT / "webgui" / "static"
if _STATIC_DIR.is_dir():
    app.add_static_files("/static", str(_STATIC_DIR))

# Generated Desk voice clips at /voice. Created eagerly rather than guarded on
# ``is_dir()`` like /static above: this directory is BUILT at runtime, so on a
# fresh clone the guard would skip the mount and every clip would 404 until a
# restart. See webgui/voice.py.
_VOICE_DIR = _REPO_ROOT / "webgui" / "data" / "voice"
try:
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    app.add_static_files("/voice", str(_VOICE_DIR))
except OSError:
    logging.getLogger("webgui").warning(
        "voice clip directory unavailable — Desk spoken alerts are off",
        exc_info=True)
```

Verify `logging` is already imported in `main.py` (it is — `sync_manual_paper_lifecycle_setting` uses it).

**Step 2: Verify the mount**

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/inspiring-sinoussi-b1cc78/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_shell.py -q)
```

Expected: the existing shell smoke tests still pass — `main.py` must still import cleanly.

**Step 3: Commit**

```bash
git add webgui/main.py
git commit -m "feat(webgui): serve generated voice clips at /voice"
```

---

## Task 6: Settings keys and the Spoken alerts card

**Files:**
- Modify: `webgui/app_settings.py` (the `DEFAULTS` dict)
- Modify: `webgui/pages/settings.py` (after the "Scanner alerts" card, ~line 135)
- Test: `webgui/tests/test_app_settings.py` (extend if it exists; else add the assertions to `test_voice.py`)

**Step 1: Write the failing test**

```python
def test_voice_settings_have_defaults():
    import app_settings
    d = app_settings.DEFAULTS
    assert d["voice_enabled"] is True
    assert d["voice_name"] == "en-US-AriaNeural"
    assert 0.0 <= d["voice_volume"] <= 1.0


def test_the_default_voice_is_one_we_offer():
    import app_settings
    import voice
    assert app_settings.DEFAULTS["voice_name"] in voice.VOICES
```

**Step 2: Run to verify it fails**

Expected: `KeyError: 'voice_enabled'`

**Step 3: Add the keys**

In `app_settings.DEFAULTS`, after the `flow_alerts_enabled` line:

```python
    # Desk spoken alerts. There is deliberately NO voice_market_hours_only key:
    # the existing alert_market_hours_only gate is honoured instead, because two
    # market-hours switches sitting beside each other is a drift hazard, not a
    # feature.
    "voice_enabled": True,                     # speak new Desk flow alerts + positions
    "voice_name": "en-US-AriaNeural",          # see voice.VOICES
    "voice_volume": 0.8,                       # 0.0-1.0
```

**Step 4: Add the Settings card**

In `webgui/pages/settings.py`, after the "Scanner alerts" card closes and before the "Desktop notifications" card:

```python
    with ui.card().classes("w-full max-w-2xl"):
        ui.label("Spoken alerts (Desk)").classes("text-subtitle1 font-bold")
        ui.label("Announce the ticker and the cause out loud when a new flow "
                 "alert or a newly-opened position appears on the Desk. Uses "
                 "the existing market-hours gate above.").classes(
                 "opacity-70 text-sm")

        v_enable = ui.switch("Enable spoken alerts", value=s["voice_enabled"])
        v_enable.on_value_change(
            lambda e: app_settings.set("voice_enabled", e.value))

        with ui.row().classes("items-center gap-4"):
            v_name = ui.select(list(voice.VOICES), label="Voice",
                               value=s["voice_name"]).classes("w-64")
            v_name.on_value_change(
                lambda e: app_settings.set("voice_name", e.value))
            v_test = ui.button("Test voice", icon="record_voice_over",
                               color=None).props("no-caps").classes(BTN_3D)

        ui.label("Volume").classes("text-sm opacity-70")
        v_vol = ui.slider(min=0, max=1, step=0.05,
                          value=s["voice_volume"]).classes("w-64")
        v_vol.on_value_change(
            lambda e: app_settings.set("voice_volume", e.value))

        ui.label("First synthesis of a phrase takes about a second; after that "
                 "it plays from a local cache. Test voice also unlocks browser "
                 "audio, which is blocked until you interact with the page.").classes(
                 "opacity-60 text-xs")
```

Wire the test button — it must synthesize off the event loop:

```python
    async def _test_voice():
        settings = app_settings.load()
        url = await run.io_bound(
            voice.ensure, "S P Y. Crossover alert, calls over.",
            settings["voice_name"])
        if url is None:
            ui.notify("Voice unavailable — check the network and edge-tts.",
                      type="warning")
            return
        # The shared alert element is fine HERE: nothing else is speaking on the
        # Settings page. The Desk uses its own element so a chime cannot cut an
        # announcement off, which is a Desk-only concern.
        ui.run_javascript(
            f"(() => {{ const a = document.getElementById('alert-audio'); "
            f"if (!a) return; a.src = '{url}'; "
            f"a.volume = {float(settings['voice_volume'])}; "
            f"a.play().catch(() => {{}}); }})()")

    v_test.on_click(_test_voice)
```

Add the imports at the top of `settings.py`: `import voice` and `from nicegui import run` (check whether `run` is already imported).

**Step 5: Run the tests**

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/inspiring-sinoussi-b1cc78/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest . -q)
```

Expected: the full webgui suite green (baseline 2320 + the new tests). `test_no_inline_style.py` must stay green — the card above uses only `.classes()`.

**Step 6: Commit**

```bash
git add webgui/app_settings.py webgui/pages/settings.py webgui/tests/
git commit -m "feat(settings): spoken-alert enable, voice picker, volume and test button"
```

---

## Task 7: `desk.py` — new-row and flag-change detection

**Files:**
- Modify: `webgui/pages/desk.py` (pure builders, near `position_flag` around line 471)
- Modify: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_desk.py`:

```python
# ── arrival detection ────────────────────────────────────────────────────────
def test_new_ids_reports_only_rows_not_seen_before():
    rows = [{"id": "c"}, {"id": "b"}, {"id": "a"}]
    assert d.new_ids(rows, {"a", "b"}) == ["c"]


def test_new_ids_preserves_row_order_so_the_newest_is_first():
    # ``flow.alert_rows`` is newest-first, and the newest new row is the one
    # that gets spoken. Order is load-bearing, not incidental.
    rows = [{"id": "c"}, {"id": "b"}, {"id": "a"}]
    assert d.new_ids(rows, set()) == ["c", "b", "a"]


def test_new_ids_counts_a_duplicated_id_once():
    rows = [{"id": "a"}, {"id": "a"}]
    assert d.new_ids(rows, set()) == ["a"]


def test_new_ids_skips_rows_with_no_id():
    assert d.new_ids([{"id": None}, {}, {"id": "a"}], set()) == ["a"]


def test_new_ids_reads_the_key_the_caller_names():
    # Positions key on position_id, flow on id. One function, not two.
    rows = [{"position_id": "p1"}]
    assert d.new_ids(rows, set(), key="position_id") == ["p1"]


def test_new_ids_of_nothing_is_empty():
    assert d.new_ids(None, set()) == []
    assert d.new_ids([], {"a"}) == []


# ── flag changes ─────────────────────────────────────────────────────────────
def test_flag_changes_reports_a_moved_flag():
    rows = [{"position_id": "p1", "flag": "AT RISK"}]
    assert d.flag_changes(rows, {"p1": "OK"}) == ["p1"]


def test_flag_changes_ignores_an_unchanged_flag():
    rows = [{"position_id": "p1", "flag": "OK"}]
    assert d.flag_changes(rows, {"p1": "OK"}) == []


def test_a_first_sighting_is_not_a_flag_change():
    # It is an ARRIVAL, and new_ids already glows it. Counting it here too
    # would give a brand-new row two overlapping glows.
    rows = [{"position_id": "p1", "flag": "OK"}]
    assert d.flag_changes(rows, {}) == []


def test_flag_map_keys_positions_by_id():
    rows = [{"position_id": "p1", "flag": "OK"}, {"position_id": None,
                                                  "flag": "RESCUE"}]
    assert d.flag_map(rows) == {"p1": "OK"}
```

**Step 2: Run to verify it fails**

Expected: `AttributeError: module 'pages.desk' has no attribute 'new_ids'`

**Step 3: Write the implementation**

Insert into `webgui/pages/desk.py` after `position_flag`:

```python
# ── arrival + change detection ───────────────────────────────────────────────
# Pure, so the whole "what is new on this screen" question is testable without
# a browser. The page-state sets these read against are seeded SILENTLY on the
# first paint — without that, navigating to the Desk announces the entire day's
# alert list and lights every row, which is exactly the trap main.py's watcher
# already documents for the scanner chime.
def new_ids(rows, seen, key="id"):
    """Ids in ``rows`` not already in ``seen``, IN ROW ORDER.

    Row order is load-bearing: the flow feed is newest-first and the newest
    arrival is the one that gets spoken, so the caller reads ``[0]``. A row with
    no id is skipped rather than given a positional key — a synthetic key would
    change identity on the next repaint and re-announce forever.
    """
    out = []
    for r in rows or ():
        rid = (r or {}).get(key) if isinstance(r, dict) else None
        if rid is None or rid in seen or rid in out:
            continue
        out.append(rid)
    return out


def id_set(rows, key="id"):
    """The ids present in ``rows`` — what ``seen`` is replaced with each paint.

    REPLACED, not unioned: the flow list is day-scoped and rolling, and a
    position that closes and reopens really is a new position. An ever-growing
    set would also never shrink on a page left open for days.
    """
    return {(r or {}).get(key) for r in rows or ()
            if isinstance(r, dict) and (r or {}).get(key) is not None}


def flag_map(rows):
    """``{position_id: flag}`` — the previous-state map ``flag_changes`` reads."""
    return {r["position_id"]: r.get("flag") for r in rows or ()
            if isinstance(r, dict) and r.get("position_id") is not None}


def flag_changes(rows, prev):
    """Position ids whose ``flag`` moved since ``prev``.

    A FIRST SIGHTING is deliberately not a change — it is an arrival, and
    ``new_ids`` already glows it. Counting it in both places would give a new
    row two overlapping glows.
    """
    out = []
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        rid = r.get("position_id")
        if rid is None or rid not in prev:
            continue
        if r.get("flag") != prev[rid]:
            out.append(rid)
    return out
```

**Step 4: Run to verify it passes**

Expected: `11 passed` for the new tests, full `test_desk.py` green.

**Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): pure new-row and flag-change detection"
```

---

## Task 8: `desk.py` — the neon glow that survives a repaint

**Files:**
- Modify: `webgui/pages/desk.py` (constants near `_ROW_RULE` ~line 1215; CSS near `_ROW` ~line 1408)
- Modify: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

```python
# ── the neon glow ────────────────────────────────────────────────────────────
def test_glow_step_starts_at_zero():
    assert d.glow_step(started=100.0, now=100.0) == 0


def test_glow_step_advances_one_class_per_second():
    assert d.glow_step(started=100.0, now=103.4) == 3
    assert d.glow_step(started=100.0, now=109.9) == 9


def test_glow_step_never_exceeds_the_last_class():
    # A rounding slip that returned 10 would emit desk-neon-10, a class with no
    # rule behind it — the animation would restart instead of finishing.
    assert d.glow_step(started=100.0, now=109.999999) == d.GLOW_STEPS - 1


def test_glow_step_is_none_once_expired():
    assert d.glow_step(started=100.0, now=110.0) is None
    assert d.glow_step(started=100.0, now=999.0) is None


def test_glow_step_is_none_for_a_row_that_never_glowed():
    assert d.glow_step(started=None, now=100.0) is None


def test_glow_classes_name_a_hue_and_a_resume_point():
    cls = d.glow_classes(("new", 100.0), now=103.0)
    assert "desk-neon" in cls and "desk-neon-new" in cls and "desk-neon-3" in cls


def test_glow_classes_are_empty_once_expired():
    assert d.glow_classes(("new", 100.0), now=120.0) == ""
    assert d.glow_classes(None, now=120.0) == ""


def test_every_glow_step_class_has_a_rule_behind_it():
    # The resume trick is silent when it breaks: a missing rule just restarts
    # the animation, which looks like a glow that never expires.
    for i in range(d.GLOW_STEPS):
        assert f".desk-neon-{i} " in d.DESK_NEON_CSS or \
               f".desk-neon-{i}{{" in d.DESK_NEON_CSS.replace(" ", "")


def test_both_glow_hues_have_a_rule():
    assert ".desk-neon-new" in d.DESK_NEON_CSS
    assert ".desk-neon-flag" in d.DESK_NEON_CSS


def test_the_animation_runs_for_the_advertised_ten_seconds():
    assert d.GLOW_SEC == 10.0
    assert f"deskNeon {d.GLOW_SEC:g}s" in d.DESK_NEON_CSS
```

**Step 2: Run to verify it fails**

Expected: `AttributeError: module 'pages.desk' has no attribute 'glow_step'`

**Step 3: Write the implementation**

Add `import time` to the imports at the top of `desk.py`, then insert after the detection block from Task 7:

```python
# ── the 10-second neon glow ──────────────────────────────────────────────────
# ⚠ THE NON-OBVIOUS PART. ``_paint_positions`` calls ``pos_body.clear()`` and
# rebuilds every row, and it runs whenever the paper account re-prices — which
# is constant during market hours. A REBUILT ELEMENT RESTARTS ITS CSS ANIMATION
# FROM ZERO, so the naive implementation glows forever: every repaint resets the
# decay and the row never goes dark.
#
# The fix is a whole-second NEGATIVE ``animation-delay``, which starts an
# animation partway through. The glow's START TIME lives in page state keyed by
# row id; the row wears ``desk-neon-N`` where N is how many seconds have already
# elapsed, so a rebuilt element RESUMES rather than restarts.
#
# Ten fixed classes rather than a computed ``[animation-delay:-3.2s]``: the
# styling standard's finite-set rule. The cost is one second of granularity on
# a ten-second decay, which is invisible.
GLOW_SEC = 10.0
GLOW_STEPS = 10

# The two things worth glowing about, and nothing else. NEW is the cyan the
# structure map already uses for spot; FLAG is the amber it uses for the flip —
# both already mean "look here" on this page.
GLOW_NEW = "new"
GLOW_FLAG = "flag"


def glow_step(started, now, span=GLOW_SEC, steps=GLOW_STEPS):
    """Which ``desk-neon-N`` class a glow started at ``started`` wears at ``now``.

    ``None`` once it has expired, or if it has not begun. Both are the same
    answer to the caller — do not glow — and collapsing them here keeps the
    check at the call site to one branch.
    """
    if started is None:
        return None
    try:
        elapsed = float(now) - float(started)
    except (TypeError, ValueError):
        return None
    if elapsed < 0 or elapsed >= span:
        return None
    # ``min`` is not belt-and-braces: float division at the very top of the
    # range can yield exactly ``steps``, and ``desk-neon-10`` has no rule behind
    # it — the animation would silently restart instead of finishing.
    return min(steps - 1, max(0, int(elapsed / span * steps)))


def glow_classes(entry, now):
    """The class string for a glowing row, or ``''``.

    ``entry`` is the ``(kind, started)`` tuple held in page state, or ``None``.
    """
    if not entry:
        return ""
    kind, started = entry
    step = glow_step(started, now)
    if step is None:
        return ""
    return f"desk-neon desk-neon-{kind} desk-neon-{step}"


def prune_glows(glow, now):
    """Drop expired entries. Mutates and returns ``glow``.

    Called once per paint. Without it the map grows for the life of the tab —
    small, but it is also the only thing that makes the map's size mean
    something when debugging.
    """
    for rid in [k for k, v in glow.items() if glow_step(v[1], now) is None]:
        glow.pop(rid, None)
    return glow


# The ONE escape hatch this page is already allowed (it injects
# CONSOLE_KEYFRAMES_CSS beside this). A keyframes animation cannot be a utility
# class, and ``--neon`` is a plain custom property inside a real stylesheet —
# NOT a Tailwind arbitrary value, which is where the documented ``var(...)``
# JIT limitation bites.
_NEON_STEPS_CSS = "\n".join(
    f".desk-neon-{i} {{ animation-delay: -{i}s; }}" for i in range(GLOW_STEPS))

DESK_NEON_CSS = f"""
@keyframes deskNeon {{
  0%   {{ box-shadow: inset 0 0 0 1px var(--neon), 0 0 18px -2px var(--neon);
          background-color: rgba(255,255,255,.055); }}
  65%  {{ box-shadow: inset 0 0 0 1px var(--neon), 0 0 11px -5px var(--neon);
          background-color: rgba(255,255,255,.022); }}
  100% {{ box-shadow: inset 0 0 0 0 transparent, 0 0 0 0 transparent;
          background-color: transparent; }}
}}
.desk-neon {{ animation: deskNeon {GLOW_SEC:g}s linear forwards;
              border-radius: 3px; }}
.desk-neon-{GLOW_NEW} {{ --neon: {SPOT_HEX}; }}
.desk-neon-{GLOW_FLAG} {{ --neon: {FLIP_HEX}; }}
{_NEON_STEPS_CSS}
"""
```

Note this block must sit **after** `SPOT_HEX` / `FLIP_HEX` are defined (they are, around line 1250) — so place the whole glow section down there with the other display constants rather than up beside `position_flag`. Keep `glow_step` / `glow_classes` / `prune_glows` next to the CSS.

**Step 4: Inject the CSS**

In `render()`, beside the existing injection (`desk.py:1742`):

```python
    ui.add_css(CONSOLE_KEYFRAMES_CSS)
    # The 10-second arrival glow. Same justification as the line above: a
    # keyframes animation cannot be expressed as a utility class.
    ui.add_css(DESK_NEON_CSS)
```

**Step 5: Run the tests**

Expected: the ten new glow tests pass; `test_no_inline_style.py` still green.

**Step 6: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): 10s neon arrival glow that resumes across repaints"
```

---

## Task 9: Wire the glow into the two panels

**Files:**
- Modify: `webgui/pages/desk.py` — `render()`'s `state` dict (~line 1747), `_paint_flow` (~2150), `_flow_row` (~2178), `_paint_positions` (~2198), `_position_row` (~2234), `_paint` (~2293)

**Step 1: Extend the page state**

```python
    state = {"versions": {}, "data": {},
             # Arrival tracking. ``seen_*`` are REPLACED each paint (see
             # ``id_set``); ``glow`` maps row id -> (kind, started_monotonic).
             # ``first`` is what makes the initial paint silent and dark.
             "seen_flow": set(), "seen_pos": set(), "pos_flags": {},
             "glow": {}, "first": True, "speak": []}
```

**Step 2: Detect before painting**

Add beside the painters:

```python
    def _detect_flow():
        """Fold new flow arrivals into the glow map; return the utterance.

        Runs over the FULL alert list, not the nine rows the panel draws. A
        burst of ten would otherwise push arrivals off the bottom unseen, and
        they would announce themselves later when the list shortened.
        """
        rows = _flow.alert_rows(_view("options:flow_alerts"))
        ids = new_ids(rows, state["seen_flow"])
        state["seen_flow"] = id_set(rows)
        if state["first"] or not ids:
            return None
        now = time.monotonic()
        for rid in ids:
            state["glow"][rid] = (GLOW_NEW, now)
        newest = next((r for r in rows if r.get("id") == ids[0]), None)
        return _voice.flow_phrase(newest, extra=len(ids) - 1) if newest else None

    def _detect_positions():
        """Same, for the three books — plus the silent flag-change glow."""
        rows = position_rows(_view("options:paper_account"),
                             _view("options:driver_paper_account"),
                             _view("options:captured"))
        ids = new_ids(rows, state["seen_pos"], key="position_id")
        moved = flag_changes(rows, state["pos_flags"])
        state["seen_pos"] = id_set(rows, key="position_id")
        state["pos_flags"] = flag_map(rows)
        if state["first"]:
            return None
        now = time.monotonic()
        for rid in ids:
            state["glow"][rid] = (GLOW_NEW, now)
        # A flag move glows but never speaks — decided in the design doc. It is
        # a state a position was ALREADY in the book for; the voice is reserved
        # for things that were not on the screen a moment ago.
        for rid in moved:
            state["glow"].setdefault(rid, (GLOW_FLAG, now))
        if not ids:
            return None
        newest = next((r for r in rows if r.get("position_id") == ids[0]), None)
        return _voice.position_phrase(newest, extra=len(ids) - 1) if newest else None
```

Add `from pages import ...` — actually `voice` is a top-level webgui module, so: `import voice as _voice` beside `import bus_client` at the top of `desk.py`.

**Step 3: Apply the glow class in both row builders**

In `_flow_row`, change the element construction to:

```python
    def _flow_row(row):
        glow = glow_classes(state["glow"].get(row.get("id")), time.monotonic())
        el = ui.element("div").classes(
            f"{FLOW_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06] {glow}")
```

In `_position_row`, the same with `row.get("position_id")`.

**Step 4: Prune and re-arm the repaint**

A glow must fade even when nothing else changes, so the panel needs a repaint while any glow is live. Add a timer beside the poll:

```python
    @guard
    def _tick_glow():
        """Repaint the two arrival panels while any glow is still burning.

        The version poll only repaints on DATA movement, and a flow alert at
        14:31 may be the last thing that moves for a minute — without this the
        row would stay lit at whatever step it was drawn at. Costs nothing when
        the map is empty, which is almost always.
        """
        if not state["glow"]:
            return
        before = set(state["glow"])
        prune_glows(state["glow"], time.monotonic())
        # Repaint on every tick a glow is live (the step class must advance),
        # and once more after the last one expires (to clear it).
        _paint_flow()
        _paint_positions()
        if not state["glow"] and before:
            return
```

and register it after the existing timers:

```python
    ui.timer(1.0, _tick_glow)
```

**Step 5: Hook detection into `_paint`**

```python
    def _paint(payloads):
        """Merge the changed views in, then repaint only what depends on them."""
        state["data"].update(payloads)
        changed = set(payloads)
        state["speak"] = []
        # Detection FIRST: the painters read state["glow"], so a row must be
        # marked before the paint that is supposed to draw it lit.
        if changed.intersection(_REGION_VIEWS["flow"]):
            said = _detect_flow()
            if said:
                state["speak"].append(said)
        if changed.intersection(_REGION_VIEWS["positions"]):
            said = _detect_positions()
            if said:
                state["speak"].append(said)
        prune_glows(state["glow"], time.monotonic())
        for region, deps in _REGION_VIEWS.items():
            if changed.intersection(deps):
                painters[region]()
```

**Step 6: Seed the first paint silently**

At the bottom of `render()`, wrap the seed paint:

```python
    _tick_clock()
    _paint(seed)
    # Everything on screen at first paint is history, not an arrival. Clearing
    # the flag AFTER the seed paint is what makes it so.
    state["first"] = False
```

**Step 7: Run the full webgui suite**

```bash
(cd "D:/WebGUI Trading with Schwab/.claude/worktrees/inspiring-sinoussi-b1cc78/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest . -q -rf)
```

Expected: green, no new failures against the 2320 baseline.

**Step 8: Commit**

```bash
git add webgui/pages/desk.py
git commit -m "feat(desk): glow new flow alerts and positions for 10 seconds"
```

---

## Task 10: Speak — the audio element, the queue and the unlock

**Files:**
- Modify: `webgui/pages/desk.py` — `render()` head, `_poll`

**Step 1: Add the audio element and the JS queue**

Module-level in `desk.py`:

```python
# The Desk speaks through its OWN audio element, not main.py's shared
# ``alert-audio``. Sharing one would let a scanner chime cut an announcement off
# mid-sentence, and the chime is app-wide while this is one page.
#
# ``emitEvent`` is NiceGUI's JS-to-Python channel; the blocked event is how the
# unlock chip learns that autoplay was refused. That refusal is COMPLETELY
# SILENT otherwise — the play() promise just rejects — so without this the
# feature looks broken on every fresh tab with nothing in any log.
DESK_VOICE_JS = """
window.__deskVoice = window.__deskVoice || {q: [], busy: false};
window.__deskSpeak = function (urls, vol) {
  const v = window.__deskVoice;
  const el = document.getElementById('desk-voice');
  if (!el) return;
  urls.forEach(u => v.q.push(u));
  if (v.busy) return;
  const next = () => {
    if (!v.q.length) { v.busy = false; return; }
    v.busy = true;
    el.src = v.q.shift();
    el.volume = vol;
    el.play().catch(() => {
      v.q.length = 0; v.busy = false;
      emitEvent('desk_voice_blocked', {});
    });
  };
  el.onended = next;
  el.onerror = next;
  next();
};
"""
```

`el.onerror = next` matters: a 404 on one clip must not wedge the queue.

In `render()`, beside the CSS injections:

```python
    ui.add_head_html(f"<script>{DESK_VOICE_JS}</script>")
    ui.html('<audio id="desk-voice" preload="auto"></audio>')
```

**Step 2: The unlock chip**

In the Desk header row, after the clock:

```python
        unlock = ui.button("Enable voice", icon="volume_off", color=None) \
            .props("no-caps dense flat") \
            .classes(f"text-[10px] tracking-[.14em] {CON_WARN}")
        unlock.set_visibility(False)
```

and:

```python
    @guard
    def _voice_blocked():
        """Autoplay was refused. Offer the one gesture that fixes it."""
        unlock.set_visibility(True)

    @guard
    def _unlock_voice():
        """A click IS the gesture, so simply playing here unlocks the element."""
        unlock.set_visibility(False)
        ui.run_javascript(
            "(() => { const a = document.getElementById('desk-voice'); "
            "if (a) a.play().catch(() => {}); })()")

    ui.on("desk_voice_blocked", lambda _e: _voice_blocked())
    unlock.on_click(_unlock_voice)
```

**Step 3: Speak from the poll**

`ensure` is blocking, so synthesis happens off the loop in `_poll`, after `_paint` has queued the phrases:

```python
    async def _speak_pending():
        """Turn the phrases ``_paint`` queued into clips and play them.

        Synthesis is BLOCKING (~600 ms on a cache miss), so it goes through
        ``run.io_bound`` — never on the event loop, which every other page on
        this app shares.
        """
        phrases, state["speak"] = state["speak"], []
        if not phrases:
            return
        settings = app_settings.load()
        if not settings.get("voice_enabled"):
            return
        if settings.get("alert_market_hours_only") and \
                not _alerts.in_market_hours(datetime.now(_CT)):
            return
        urls = []
        for text in phrases:
            url = await run.io_bound(voice_ensure, text,
                                     settings.get("voice_name"))
            if url:
                urls.append(url)
        if urls:
            vol = float(settings.get("voice_volume") or 0.8)
            ui.run_javascript(
                f"window.__deskSpeak && window.__deskSpeak({urls!r}, {vol})")
```

`{urls!r}` produces a Python list repr with single quotes, which is valid JS. Confirm with a unit test on the built string rather than trusting it.

Add `import app_settings`, `import voice`, and `from webgui import alerts as _alerts` — check the actual import style used elsewhere in `webgui/` (it is a flat module dir, so `import alerts as _alerts` and `import app_settings`).

Call it at the end of `_poll`:

```python
        _paint(payloads)
        await _speak_pending()
```

**Step 4: Prewarm at first paint**

After the seed paint in `render()`:

```python
    # Warm the phrase cache for the watchlist in the background, so a live alert
    # plays from disk rather than paying ~600 ms of synthesis. Once per process:
    # the cache is on disk and survives restarts, so a second page build has
    # nothing to do.
    _prewarm_once(seed.get("options:matrix"), app_settings.get("voice_name"))
```

with:

```python
_PREWARMED = {"done": False}


def _prewarm_once(matrix_view, voice_name):
    """Kick the background prewarm for the watchlist symbols. Never raises."""
    if _PREWARMED["done"]:
        return
    _PREWARMED["done"] = True
    try:
        rows = (matrix_view or {}).get("rows") or []
        symbols = [r.get("symbol") for r in rows if isinstance(r, dict)]
        voice.prewarm([s for s in symbols if s], voice_name)
    except Exception:  # noqa: BLE001 — a cold cache is not an error.
        pass
```

**Step 5: Test the wiring**

Add to `webgui/tests/test_desk.py`:

```python
def test_the_speak_call_emits_valid_javascript_for_a_url_list():
    # A Python list repr is valid JS array syntax, but only for these strings —
    # pin it, since the failure mode is a silent JS SyntaxError in the console.
    urls = ["/voice/abc.mp3", "/voice/def.mp3"]
    js = f"window.__deskSpeak && window.__deskSpeak({urls!r}, 0.8)"
    assert js == ("window.__deskSpeak && window.__deskSpeak("
                  "['/voice/abc.mp3', '/voice/def.mp3'], 0.8)")


def test_the_voice_js_recovers_from_a_failed_clip():
    # onerror must advance the queue, or one 404 wedges every later
    # announcement for the life of the tab.
    assert "el.onerror" in d.DESK_VOICE_JS
    assert "emitEvent('desk_voice_blocked'" in d.DESK_VOICE_JS
```

**Step 6: Run the full suite**

Expected: green against the 2320 baseline.

**Step 7: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): speak new flow alerts and positions, with an autoplay unlock"
```

---

## Task 11: Documentation

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/webgui-routes.md` (the `/desk` section)
- Modify: `webgui/page_help.py` (the Desk entry)
- Modify: `docs/manuals/` (User Guide — Settings section)

**Step 1: CHANGELOG**

A dated entry naming what shipped, the three findings from the design doc, and the new setting keys.

**Step 2: `docs/webgui-routes.md`**

Under `/desk`: what speaks, what only glows, the 10-second decay and why it resumes rather than restarts, and where the clips live.

**Step 3: `page_help.py`**

The Desk hover guide gains a sentence on the spoken alerts and the unlock chip. **This is the most-read prose in the app and the first thing to rot** — it is updated in this change, not later.

**Step 4: User Guide**

The Settings chapter gains the Spoken alerts controls and the autoplay caveat. A user-visible behaviour change lands in the manual, not only in the CHANGELOG.

**Step 5: Rebuild the manuals**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" build_docs.py
```

**Step 6: Commit**

```bash
git add docs/ webgui/page_help.py
git commit -m "docs: Desk spoken alerts and neon arrival glow"
```

---

## Task 12: Live verification in dev

**THE DEVELOPMENT RULE applies.** Tests passing is not verification for anything with a runtime surface, and this feature is almost entirely runtime surface — autoplay, CSS animation resume, and network synthesis are all invisible to pytest.

**Step 1: Land the branch in dev**

Commit everything in the worktree, then fast-forward `Using_Highcharts` and `main` in the dev checkout (`D:\WebGUI Trading with Schwab`). Do **not** touch the prod checkout.

**Step 2: Restart the dev webgui**

Dev runs on `:9500`. Restart it so the new code is live, and confirm the port actually bound — a failed bind is silent and leaves the OLD server serving, which reads exactly like a broken change. Check the launcher log for `[Errno 10048]`.

**Step 3: Verify the clip cache fills**

```bash
ls "D:/WebGUI Trading with Schwab/webgui/data/voice/" | head
```

Expected: mp3 files appearing within a minute or two of the page opening (the background prewarm).

**Step 4: Verify in the browser**

Open `http://127.0.0.1:9500/desk`. Confirm, in order:

1. **Nothing speaks and nothing glows on load** — the first-paint seed is silent.
2. Click **Enable voice** if the chip appeared (it will on a fresh tab).
3. Trigger an arrival. Off-hours, the honest way is to push a synthetic alert onto the bus rather than wait for the tape:
   ```python
   from shared.bus import Bus
   b = Bus()
   v = b.cache_get("cache:options:flow_alerts").payload
   v["alerts"].append({"id": "probe-1", "ts": 1755800000, "symbol": "SPY",
                       "type": "crossover", "side": "calls_over",
                       "text": "probe", "premium": 1234567})
   b.cache_set("cache:options:flow_alerts", v, event="events:options:flow_alerts")
   ```
   Expected: within two seconds the row appears at the top of the Flow panel, glowing cyan, and the voice says "S P Y. Crossover alert, calls over."
4. **Watch the glow decay and stop** — it must go dark at ten seconds and stay dark. This is the repaint-resume behaviour; if it pulses or never fades, `glow_step` or the step classes are wrong.
5. Push a burst of four alerts in one `cache_set`. Expected: one utterance, the newest, ending "Plus 3 more."
6. Toggle **Settings → Spoken alerts → Enable** off, push another alert. Expected: it glows, it does not speak.

**Step 5: Confirm the failure path**

Disconnect the network (or point `voice.DEFAULT_VOICE` at a nonsense name), push an alert. Expected: **the row still glows**, nothing speaks, and `logs/webgui.out.log` carries exactly ONE warning, not one per tick.

**Step 6: Report**

State plainly what was verified and what was not. If any step could not be exercised (an off-hours tape gives no real flow alerts), say so rather than implying it was seen working.

---

## What is deliberately NOT in this plan

- **`/options/flow` and the paper pages.** Scoped to `/desk`. The builders in `voice.py` are page-agnostic, so extending later is small.
- **Position state changes speaking.** They glow amber, silently. Decided in the design doc.
- **A separate voice market-hours switch.** The existing `alert_market_hours_only` is honoured.
- **Position phrases in the prewarm.** A new position is user-initiated, so the user is already at the screen and 600 ms costs nothing.
