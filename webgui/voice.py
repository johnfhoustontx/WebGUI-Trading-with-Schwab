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

# The shared numeric vocabulary, and the ONLY thing this module takes from
# ``pages``. ``pages/__init__.py`` is a docstring and ``fmt`` imports ``math``,
# so this costs nothing and is free of page side effects — but it is also the
# reason the alert labels further down are still a hand-kept COPY rather than an
# import: ``pages.options.flow`` is a PAGE, and the prewarm runs before any page
# is built. ``num`` specifically (not ``float_or``) because every value reaching
# here feeds a decision — speak this, or fall back — and ``num`` is the half of
# that pair that answers None for a NaN and for a bool.
from pages.fmt import num as _num

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


# ── the numeric vocabulary ───────────────────────────────────────────────────
# Everything below is spoken, so the only evidence that counts is a LISTENING
# test, and every rule here came out of one against real strikes.


def say_number(v):
    """A strike or a price, spoken the way a trader hears it. ``''`` for junk.

    Leading digits go one at a time and the last two as a PAIR, because a neural
    voice reads "205" as "two hundred and five" while the strike is "two oh
    five" — and at five digits ("21500") the plain reading is a mouthful nobody
    can hold. A trailing ``00`` becomes "hundred" rather than two spoken zeros,
    and a fraction becomes ". point 5", where the leading period is what makes
    the voice pause before it.

    A number of one or two digits is left completely alone: "5" already reads
    correctly, and "5" spelled out is not an improvement on anything.

    ``_num`` is the guard, so ``None``, ``""``, ``"abc"``, NaN, infinity and —
    the documented one — ``bool`` all return ``''`` rather than a number nobody
    supplied. A negative keeps its sign out loud instead of quietly becoming its
    own absolute value; nothing feeds this a negative today, which is exactly
    why the honest branch is the cheap one to write.
    """
    f = _num(v)
    if f is None:
        return ""
    whole = int(abs(f))
    frac = abs(f) - whole
    s = str(whole)
    if len(s) <= 2:
        out = s
    else:
        lead, pair = s[:-2], s[-2:]
        out = " ".join(lead) + (" hundred" if pair == "00" else f" {pair}")
    if frac:
        # ⚠ The strip can empty the string — ``f"{0.99999:.4f}"`` is "1.0000" —
        # and appending ". point " with nothing after it is the one thing a
        # spoken alert is least allowed to do.
        digits = f"{frac:.4f}".split(".")[1].rstrip("0")
        if digits:
            out += ". point " + " ".join(digits)
    return f"minus {out}" if f < 0 else out


def say_expiry(expiry, dte):
    """``'0-D T E'`` or ``'8 - 28'`` from an ISO date. ``''`` when unusable.

    The mirror of ``pages.options.flow._exp_short``, which prints "0DTE" and
    "08/28" — same two branches, different medium. Two differences are
    deliberate. The zero-DTE case is SPELLED, since "0DTE" read as a word is a
    noise. And an unreadable date returns ``''`` here where the table falls back
    to the raw string: a reader can see that an odd cell is odd, while
    "two thousand and twenty six dash zero eight" is simply unintelligible, and
    an empty answer routes the caller to the short form instead.

    ``_num(dte) == 0`` rather than ``dte == 0`` because ``False == 0`` is True in
    Python, and a flag arriving where a day count belongs must not be announced
    as a 0DTE contract.
    """
    if _num(dte) == 0:
        return "0-D T E"
    parts = str(expiry).split("-")
    if len(parts) != 3:
        return ""
    try:
        month, day = int(parts[1]), int(parts[2])
    except (TypeError, ValueError):
        return ""
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    # Spaced hyphen: "8-28" runs together into something that is not a date.
    return f"{month} - {day}"


def say_entry(v):
    """``'entry 56 cent credit'`` / ``'entry 1 dollar 25 debit'``. ``''`` for junk.

    THE SIGN PICKS THE WORD, and that is the whole reason this function exists
    rather than a format string: the paper book stores a debit as a NEGATIVE
    ``entry_credit`` (see the paper-representation note in CLAUDE.md), so a
    phrase that dropped the sign would announce a debit as a credit — the most
    expensive sentence this feature could say.

    Under a dollar it speaks cents, at or above it speaks "N dollar NN", which
    is how the price is said at a desk. A round dollar drops the trailing zeros
    ("2 dollar", not "2 dollar 0"), and a value that rounds up through 99 cents
    becomes a dollar rather than "100 cent".

    A genuine ZERO is spoken, not swallowed. Absent and zero are different facts
    (``pages/fmt.py``'s governing rule) and treating one as the other would drop
    the whole sentence back to the short form over a number that was there.
    """
    f = _num(v)
    if f is None:
        return ""
    word = "debit" if f < 0 else "credit"
    try:
        # ``_num`` passes any FINITE float, and 1.7e308 × 100 is not finite —
        # ``round(inf)`` then raises ``OverflowError``, the same edge
        # ``more_tail`` catches around ``int(inf)``. Nothing here may raise.
        dollars, cents = divmod(int(round(abs(f) * 100)), 100)
    except (OverflowError, ValueError):
        return ""
    if not dollars:
        return f"entry {cents} cent {word}"
    amount = f"{say_number(dollars)} dollar"
    if cents:
        amount += f" {cents}"
    return f"entry {amount} {word}"


def say_strikes(text):
    """``'207.5/205'`` -> ``'2 07. point 5, 2 05'``. ``''`` when there is none.

    Reads the string ``desk.strikes_text`` ALREADY built for the panel rather
    than re-deriving a pair from the raw short/long fields — that helper has an
    iron-condor fallback of its own, and a second derivation here would be free
    to disagree with the row the listener is looking at.

    A single leg speaks its one strike (a long call is not a malformed spread),
    and an unusable half is dropped rather than taking the usable one with it.
    ``strikes_text`` returns an em-dash when there is no pair at all, which
    parses to nothing and routes the caller to the short form.
    """
    if not isinstance(text, str):
        return ""
    return ", ".join(s for s in (say_number(p) for p in text.split("/")) if s)


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
    """``'N D X. Unusual volume, 0-D T E 7 15 Put.'``

    Every field is read straight off the row that
    ``pages.options.flow.alert_rows`` built, so the spoken words are the SAME
    words the panel prints. That is not tidiness — the Desk's governing rule is
    that it composes and never re-derives, and a spoken vocabulary drifting
    from the printed one would be the documented sectors-vs-rotation bug in a
    new place. It is also why ``strike``/``expiry``/``dte`` were added to
    ``alert_rows`` rather than read here out of the raw payload.

    **Two forms, chosen by what the alert CARRIES.** Unusual volume and an
    outsized bet name a specific contract, so they say it: kind, expiry, strike, side,
    with the word "alert" dropped (the detail earned its place) and the side
    moved AFTER the strike it belongs to. A crossover is a symbol-level fact and
    a gamma flip a book-level one — neither has a contract to name, so both keep
    the original short form unchanged.

    **The fallback is SHORTER, never HALF.** A contract-carrying alert whose
    strike or expiry will not parse drops to the short form rather than emitting
    "Outsized bet, 8 - 28  Call." with a hole in it. The rows arrive off a cache
    read, so this is a live path and not a hypothetical: a terse alert is worth
    having, a broken sentence is not, and silence is worse than both. Which is
    also why the choice is made on the PARSED values and not on the alert kind —
    the degrade path and the contract path are then the same one line of code,
    and cannot drift apart.
    """
    d = row if isinstance(row, dict) else {}
    kind = str(d.get("kind") or "Flow").strip() or "Flow"
    side = str(d.get("side") or "").strip()
    strike = say_number(d.get("strike"))
    expiry = say_expiry(d.get("expiry"), d.get("dte"))
    if strike and expiry and side:
        body = f"{kind}, {expiry} {strike} {side}"
    else:
        body = f"{kind} alert" + (f", {side.lower()}" if side else "")
    return _sentence(d.get("symbol"), body, extra)


def position_phrase(row, extra=0):
    """``'S P Y. New position, put credit spread. 2 07. point 5, 2 05, 8 - 31,
    entry 56 cent credit.'``

    Phrases a position as an ARRIVAL — the wording assumes the caller only
    reaches here for a row genuinely new to the book. Choosing which rows those
    are is the Desk's job, not this module's; the design has a flag change
    (OK -> AT RISK -> RESCUE) glow but stay silent.

    Reads ``desk.position_rows``' own fields, including the ``strikes`` STRING
    that panel already renders — see ``say_strikes``. Same all-or-nothing rule
    as ``flow_phrase``: a row missing any of strikes, expiry or entry falls back
    to the short form rather than reading a sentence with a gap in it.
    """
    d = row if isinstance(row, dict) else {}
    strat = str(d.get("strategy") or "").replace("_", " ").strip().lower()
    body = f"New position, {strat}" if strat else "New position"
    strikes = say_strikes(d.get("strikes"))
    expiry = say_expiry(d.get("expiration"), d.get("dte"))
    entry = say_entry(d.get("entry_credit"))
    if strikes and expiry and entry:
        body = f"{body}. {strikes}, {expiry}, {entry}"
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

# ...but 20 s is a BACKGROUND budget, and there is one caller for whom it is
# wildly wrong. The Desk's poll awaits its speak step before it can sleep, so a
# synthesis that waits is a Desk that fetches nothing — 20 s × the two phrases
# one paint can queue is FORTY SECONDS of a landing page that has stopped
# updating, to gain a clip nobody is waiting on. 3 s is comfortably past the
# 2.4 s slowest measured synthesis and caps that worst case at ~6 s. The prewarm
# keeps the long budget: it is on a daemon thread, where waiting costs nobody.
LIVE_SYNTH_TIMEOUT_SEC = 3.0

# Abandoned ``*.part`` files older than this are swept at prewarm. A daemon
# prewarm thread killed at interpreter shutdown never runs its ``finally``, and
# CACHE_DIR is SERVED at /voice — nothing else would ever remove them.
_PART_MAX_AGE_SEC = 3600.0

# Warn ONCE per process, not once per tick. A dead network on a page that polls
# every two seconds would otherwise write 43,200 identical tracebacks a day.
_WARNED = {"done": False}

# A short-lived breaker over the SYNTHESIS CALL ONLY. Bounding one call is not
# enough on its own: a burst of two phrases against a dead endpoint pays the
# timeout twice, and the next burst pays it again, every time. After a failure
# the endpoint is left alone for a minute, so a genuine outage costs one stutter
# rather than one per burst — and a minute is short enough that a transient blip
# heals itself without anybody restarting anything.
#
# Deliberately NOT wrapped around the whole of ``ensure``: a cache HIT touches
# no network and must always be served, and a per-phrase failure that is not the
# endpoint's fault (a lone surrogate in one payload) must not silence every
# other phrase for a minute.
BREAKER_SEC = 60.0
_BREAKER = {"until": 0.0}


def reset_warning():
    """Re-arm the one-shot failure warning (test helper)."""
    _WARNED["done"] = False


def reset_breaker():
    """Close the synthesis breaker (test helper).

    Tests MUST call this between a failing case and a succeeding one, or the
    first test's tripped breaker silently suppresses the second's synthesis —
    a cross-test dependency that would look like a flake.
    """
    _BREAKER["until"] = 0.0


def _breaker_open(now=None):
    """Whether a recent synthesis failure is still being backed off."""
    return (time.monotonic() if now is None else now) < _BREAKER["until"]


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


def _synthesize(text, voice_name, rate, dest, timeout=None):
    """Write ONE mp3 to ``dest``. Raises on any failure — ``ensure`` degrades.

    ``timeout`` defaults to ``SYNTH_TIMEOUT_SEC``; the Desk's live path passes
    the much shorter ``LIVE_SYNTH_TIMEOUT_SEC`` (see that constant for why).

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

    budget = SYNTH_TIMEOUT_SEC if timeout is None else timeout

    async def _run():
        await asyncio.wait_for(
            edge_tts.Communicate(text, voice_name, rate=rate).save(str(tmp)),
            budget)

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


def ensure(text, voice_name=None, rate=RATE, warn=True, timeout=None):
    """Local URL for the phrase's clip, synthesizing on a miss. ``None`` on failure.

    BLOCKING on a miss — measured end-to-end at ~0.9-2.4 s for a Desk-length
    phrase, so callers on the event loop MUST go through
    ``nicegui.run.io_bound``. A cache hit is one ``stat`` and one ``sha1``.

    ``warn=False`` opts out of the one-shot failure warning. The prewarm passes
    it: that warning slot belongs to the live path, which is the one a user can
    actually hear go quiet.

    ``timeout`` bounds the synthesis call for THIS caller only; ``None`` takes
    the background ``SYNTH_TIMEOUT_SEC``. It is deliberately per-call and not a
    module setting, because the two callers want opposite things — see
    ``LIVE_SYNTH_TIMEOUT_SEC``. The parameter is keyword-friendly and last, so
    every existing three-positional-argument call site is untouched.
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
        if _breaker_open():
            return None     # the endpoint failed within the last BREAKER_SEC;
                            # paying the timeout again proves nothing
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _synthesize(text, voice_name, rate, dest, timeout)
            if not _usable(dest):
                raise OSError("synthesis produced no audio")
        except Exception:
            _BREAKER["until"] = time.monotonic() + BREAKER_SEC
            raise
        _BREAKER["until"] = 0.0
        return url
    except Exception:  # noqa: BLE001 — every failure mode is the same: silence.
        # Losing a race is not a failure. Two threads can synthesize the same
        # phrase at once (the prewarm daemon and a live tick); the loser's
        # ``tmp.replace(dest)`` fails with PermissionError [WinError 5] once the
        # winner's clip has an open handle on it. A good clip now exists — serve
        # it rather than go silent and burn the warning on a success.
        if dest is not None and _usable(dest):
            # ...and close the breaker again: a clip exists, so the endpoint is
            # demonstrably alive and this failure was the race, not an outage.
            _BREAKER["until"] = 0.0
            return f"{URL_PREFIX}/{dest.name}"
        if warn and not _WARNED["done"]:
            _WARNED["done"] = True
            logging.getLogger("webgui").warning(
                "voice synthesis unavailable — Desk spoken alerts are off",
                exc_info=True)
        return None


# The (kind, side) pairs the flow panel can produce, as DISPLAY labels — the
# same eight ``pages.options.flow`` maps its four types and their sides onto.
# Restated here rather than imported because the flow module is a PAGE and the
# prewarm runs before any page is built.
# ``test_voice.test_all_causes_cover_every_pair_the_flow_page_can_emit`` is
# what keeps the copy honest — it compares this tuple against ``flow._TONE``.
_ALL_CAUSES = (("Premium shift", "Calls over"), ("Premium shift", "Puts over"),
               ("Unusual volume", "Call"), ("Unusual volume", "Put"),
               ("Hedging flip", "Now damping"), ("Hedging flip", "Now amplifying"),
               ("Outsized bet", "Call"), ("Outsized bet", "Put"))

# The kinds whose phrase embeds a CONTRACT (see ``flow_phrase``). Their phrase
# space is the option chain, so it is unbounded and nothing in it can be warmed
# in advance.
CONTRACT_KINDS = ("Unusual volume", "Outsized bet")

# ...which is why the prewarm list is the OTHER four. Warming the contract kinds
# synthesized "N D X. Unusual volume alert, put." — a sentence no live alert
# produces any more, since a real one always carries a strike and an expiry. It
# was half the prewarm's network, disk and time, spent on clips that could never
# be played. DERIVED rather than written out a second time: a hand-kept subset
# of a hand-kept copy is two chances to rot instead of one.
FLOW_CAUSES = tuple((kind, side) for kind, side in _ALL_CAUSES
                    if kind not in CONTRACT_KINDS)


def prewarm_texts(symbols):
    """Every WARMABLE flow phrase the given symbols can produce.

    Flow only, and only the contract-less kinds (see ``FLOW_CAUSES``). A new
    position is a thing the user just did, so they are already looking at the
    screen and a couple of seconds of first synthesis costs nothing; a flow
    alert arrives unbidden and is the case worth paying disk for.

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
