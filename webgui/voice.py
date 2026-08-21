"""Spoken alert clips for the Desk — a phrase to mp3 cache over edge-tts.

**Tier note.** This module imports ``edge_tts`` — lazily, inside ``_synthesize``,
so the module itself stays importable on a machine that never installed it.
``edge_tts`` is neither an engine nor a Schwab caller: it is a presentation
concern, the audio equivalent of the bundled WAVs already in
``webgui/static/sounds/``, so it does not breach the documented Tier-1 rule that
the webgui imports only ``nicegui`` + ``shared.bus`` + ``shared.contracts``. Do
not read it as a violation.

**Nothing on the public surface raises**, and that is meant categorically — a
missing package, a dead network, a hung endpoint, an unwritable cache directory,
a lone surrogate in a phrase or a non-iterable symbol list all degrade to
``None``/``[]``, which the caller reads as "no speech this tick". The row still
glows and the existing chime is untouched. That matters because the alternative
to silence is a traceback on the landing page, and because the arguments arrive
off a cache read: "that would be a caller bug" is not a defence when the caller
is a malformed payload. The one deliberate exception is the private
``_synthesize``, which raises so that ``ensure`` has something to catch.

The builders at the top are pure string work and carry the whole of the spoken
vocabulary; the cache layer below is the only part that touches the network.
"""

import hashlib
import logging
import os
import pathlib
import threading
import time

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

    A ``bool`` is refused rather than counted. ``int(True)`` is 1, so a flag
    that arrived where a count belongs would announce "Plus 1 more" — the same
    ``float(True) == 1.0`` class of bug this repo has been bitten by before, and
    the reason ``pages/fmt.py``'s ``num`` rejects bool explicitly. ``OverflowError``
    is caught alongside the parsing errors because ``int(inf)`` raises it, and
    the module's promise is that nothing here raises.
    """
    if isinstance(n, bool):
        return ""
    try:
        n = int(n or 0)
    except (TypeError, ValueError, OverflowError):
        return ""
    return f"Plus {n} more." if n > 0 else ""


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

    Phrases a position as an ARRIVAL — the wording assumes the caller only
    reaches here for a row genuinely new to the book. Choosing which rows those
    are is the Desk's job, not this module's; the design has a flag change
    (OK -> AT RISK -> RESCUE) glow but stay silent.
    """
    d = row if isinstance(row, dict) else {}
    strat = str(d.get("strategy") or "").replace("_", " ").strip().lower()
    body = f"New position, {strat}" if strat else "New position"
    return _sentence(d.get("symbol"), body, extra)


# ── the mp3 cache ────────────────────────────────────────────────────────────
# Generated clips, gitignored and regenerating on demand. They live under
# ``data/`` rather than ``static/`` precisely because they are generated: a
# build artefact in a committed asset directory is a diff nobody wants.
CACHE_DIR = pathlib.Path(__file__).resolve().parent / "data" / "voice"
URL_PREFIX = "/voice"

# ``edge_tts.Communicate.save()`` is a NETWORK call to a Microsoft endpoint, and
# it runs on a ``run.io_bound`` worker thread. edge-tts DOES carry timeouts of
# its own (7.2.8: ``connect_timeout=10``, ``receive_timeout=60``) but they are
# PER-OPERATION — a stream that keeps dribbling one chunk every 59 s never trips
# either, and 60 s alone is already three times this budget. So the wrapper is
# the only thing bounding the WHOLE call. Measured end-to-end synthesis of a
# Desk-length phrase spans ~0.9-2.4 s across samples, so 20 s is not "the
# network is slow" — it is "the endpoint is gone", and waiting past it only
# pins a worker thread.
SYNTH_TIMEOUT_SEC = 20.0

# Abandoned ``*.part`` files older than this are swept at prewarm. A daemon
# prewarm thread killed at interpreter shutdown never runs its ``finally``, and
# CACHE_DIR is SERVED at /voice — nothing else would ever remove them.
_PART_MAX_AGE_SEC = 3600.0

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

    ``usedforsecurity=False`` says what this digest is for: a cache key, not a
    signature. It silences the FIPS/Bandit-class flags that fire on any sha1 and
    changes not one byte of the output — which matters, because the digest is a
    filename ON DISK and moving it would orphan every warmed clip.

    RAISES on a lone surrogate (``.encode`` refuses one, and ``json.loads`` can
    produce one from a malformed payload without error) — which is why every
    caller here computes it inside a guard.
    """
    key = f"{voice_name or DEFAULT_VOICE}|{rate}|{text}".encode("utf-8")
    return hashlib.sha1(key, usedforsecurity=False).hexdigest() + ".mp3"


def clip_url(text, voice_name=None, rate=RATE):
    """The URL the browser fetches the clip from (see main.py's /voice mount)."""
    return f"{URL_PREFIX}/{clip_name(text, voice_name, rate)}"


def _synthesize(text, voice_name, rate, dest):
    """Write ONE mp3 to ``dest``. Raises on any failure — ``ensure`` degrades.

    Writes to a per-process, per-thread temp name and then renames, so a reader
    can never be handed a half-written clip and two concurrent synthesis calls
    for the same phrase cannot corrupt each other's output.

    The bounded wait uses ``asyncio.wait_for`` INSIDE the loop rather than a
    thread-level join, so a timeout genuinely cancels the request instead of
    orphaning a socket nobody is reading.
    """
    import asyncio

    import edge_tts

    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part")

    async def _run():
        await asyncio.wait_for(
            edge_tts.Communicate(text, voice_name, rate=rate).save(str(tmp)),
            SYNTH_TIMEOUT_SEC)

    try:
        asyncio.run(_run())
        tmp.replace(dest)
    finally:
        # The cleanup must never REPLACE the exception it is cleaning up after.
        # On Windows, unlinking a file another process holds open raises
        # PermissionError [WinError 32] — an antivirus scanner touching the
        # .part is enough — and that would surface in ``ensure``'s one-shot
        # ``exc_info=True`` warning INSTEAD of the real synthesis failure, i.e.
        # the feature's only diagnostic would name the wrong cause. (An
        # ``exists()`` pre-check would not help: it races, and ``missing_ok``
        # already covers the ordinary absent case.)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _usable(path):
    """A cached clip that is actually playable — present AND non-empty.

    The size check is not paranoia: an interrupted synthesis leaves a zero-byte
    file, and serving it would make that ONE phrase permanently silent with
    nothing in the logs to say why. ONE ``stat`` answers both questions —
    a missing file raises ``FileNotFoundError``, which is an ``OSError``.
    """
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def ensure(text, voice_name=None, rate=RATE, warn=True):
    """Local URL for the phrase's clip, synthesizing on a miss. ``None`` on failure.

    BLOCKING on a miss — measured end-to-end at ~0.9-2.4 s for a Desk-length
    phrase, so callers on the event loop MUST go through
    ``nicegui.run.io_bound``. A cache hit is one ``stat`` and one ``sha1``.

    ``warn=False`` opts out of the one-shot failure warning. The prewarm passes
    it: that warning slot belongs to the live path, which is the one a user can
    actually hear go quiet.
    """
    text = str(text or "").strip()
    if not text:
        return None
    voice_name = voice_name or DEFAULT_VOICE
    dest = None
    try:
        # Inside the guard: ``clip_name`` hashes the phrase, and a lone
        # surrogate off a malformed payload makes that encode raise.
        dest = CACHE_DIR / clip_name(text, voice_name, rate)
        url = f"{URL_PREFIX}/{dest.name}"
        if _usable(dest):
            return url
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _synthesize(text, voice_name, rate, dest)
        if not _usable(dest):
            raise OSError("synthesis produced no audio")
        return url
    except Exception:  # noqa: BLE001 — every failure mode is the same: silence.
        # Losing a race is not a failure. Two threads can synthesize the same
        # phrase at once (the prewarm daemon and a live tick); the loser's
        # ``tmp.replace(dest)`` fails with PermissionError [WinError 5] once the
        # winner's clip has an open handle on it. A good clip now exists — serve
        # it rather than go silent and burn the warning on a success.
        if dest is not None and _usable(dest):
            return f"{URL_PREFIX}/{dest.name}"
        if warn and not _WARNED["done"]:
            _WARNED["done"] = True
            logging.getLogger("webgui").warning(
                "voice synthesis unavailable — Desk spoken alerts are off",
                exc_info=True)
        return None


# The (kind, side) pairs the flow panel can produce, as DISPLAY labels — the
# same eight ``pages.options.flow`` maps its four types and their sides onto.
# Restated here rather than imported because ``voice`` must stay importable
# with no ``pages`` package on the path (the prewarm runs before any page).
# ``test_voice.test_flow_causes_cover_every_pair_the_flow_page_can_emit`` is
# what keeps the copy honest — it compares this tuple against ``flow._TONE``.
FLOW_CAUSES = (("Crossover", "Calls over"), ("Crossover", "Puts over"),
               ("Unusual activity", "Call"), ("Unusual activity", "Put"),
               ("Gamma flip", "To positive"), ("Gamma flip", "To negative"),
               ("Big delta", "Call"), ("Big delta", "Put"))


def prewarm_texts(symbols):
    """Every flow phrase the given symbols can produce — the prewarm work list.

    Flow only. A new position is a thing the user just did, so they are already
    looking at the screen and a couple of seconds of first synthesis costs
    nothing; a flow alert arrives unbidden and is the case worth paying disk
    for.

    A non-iterable ``symbols`` degrades to ``[]`` rather than raising. It would
    be tempting to call that a caller bug and let it through — but the symbol
    list reaches here off a cache read, the same source as the lone surrogate
    ``ensure`` guards against, and the module's promise is categorical.
    """
    try:
        syms = list(symbols or ())
    except TypeError:
        return []
    out = []
    for sym in syms:
        for kind, side in FLOW_CAUSES:
            out.append(flow_phrase({"symbol": sym, "kind": kind, "side": side}))
    return out


def _sweep_parts(now=None):
    """Delete abandoned ``*.part`` files. Returns how many went. Never raises.

    ``_synthesize``'s ``finally`` clears its own temp file, but a daemon prewarm
    thread killed at interpreter shutdown never reaches it. CACHE_DIR is mounted
    at ``/voice`` and nothing else ever looks at it, so the leftovers accumulate
    across restarts forever. The age floor is what keeps this from deleting a
    ``.part`` a live synthesis is still writing.
    """
    cutoff = (time.time() if now is None else now) - _PART_MAX_AGE_SEC
    swept = 0
    try:
        stale = list(CACHE_DIR.glob("*.part"))
    except OSError:
        return 0
    for part in stale:
        try:
            if part.stat().st_mtime < cutoff:
                part.unlink()
                swept += 1
        except OSError:
            continue
    return swept


def prewarm(symbols, voice_name=None):
    """Synthesize the flow phrase set in the background.

    Fire-and-forget on a daemon thread. Synthesis failure never surfaces — it
    is not an error, it just leaves the lazy path to pay the synthesis on first
    use — and it deliberately does not consume the process's one-shot warning
    (``warn=False``): a transient blip here used to silence the diagnostic for
    the LIVE path, which is the one a user can hear stop working.
    """
    _sweep_parts()
    texts = prewarm_texts(symbols)
    if not texts:
        return None

    def _run():
        for text in texts:
            if ensure(text, voice_name, warn=False) is None:
                return       # a failure here usually means the rest will fail
                             # the same way, and 15 more timeouts prove nothing

    t = threading.Thread(target=_run, name="voice-prewarm", daemon=True)
    t.start()
    return t
