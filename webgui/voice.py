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
