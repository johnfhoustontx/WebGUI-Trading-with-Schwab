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
