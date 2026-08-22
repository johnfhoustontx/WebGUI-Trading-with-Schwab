"""Direction clearance — what the tape permits, per side.

**Why the short side needs this and the long side does not.** The swing model's
labels are 20-day forward EXCESS returns vs SPY, so a bottom-band name is
predicted to LAG the index — not to fall. In a rising tape a naked short on a
perfectly correct SELL read still loses money, and this repo's own autonomous
driver already paid that tuition selling call spreads into a rally. So a short
read has to clear one more hurdle than a long one: what the market is doing.

Three states, and the middle one is the important one:

``cleared``        the expression may be directional
``relative_only``  the read is real but must be expressed relatively — paired
                   against the other side, or as premium beyond a wall —
                   because the tape is against a directional version of it
``blocked``        do not express it at all

**Everything fails conservative.** An unknown SPY trend, a missing regime and a
STALE regime all land on ``relative_only`` for the short side. Reading a
four-day-old "Softening" as permission is precisely how a dead service
authorizes directional shorts into a tape that has since turned back up.

Longs are never ``blocked`` here: a long in a downtrend is a worse trade, not a
forbidden one, and the model still ranks names cross-sectionally. Demote, don't
block. Pure — the SPY series and the regime payload are fetched elsewhere.
"""
import datetime as dt

import pandas as pd

DMA_WINDOW = 200
SLOPE_LOOKBACK = 20

# Four days tolerates a normal weekend plus a holiday Monday while still
# catching a service that has genuinely stopped. Erring long here would be the
# dangerous direction, so it is deliberately not generous.
MAX_REGIME_AGE_HOURS = 96.0

# The app's own words for a committed downward direction. Kept as a set rather
# than a sign test because the payload carries BOTH a label and a numeric
# direction, and either alone can be absent.
_DOWNWARD_LABELS = {"softening", "retreating", "breakdown"}


def spy_trend(spy_close, window=DMA_WINDOW, slope_lookback=SLOPE_LOOKBACK):
    """``{above_200dma, rising_200dma}``, each True/False/**None**.

    None means "not enough history to say". It must not collapse to False:
    False reads as "below the 200-DMA", which is one of the conditions that
    CLEARS directional shorts."""
    unknown = {"above_200dma": None, "rising_200dma": None}
    try:
        s = pd.Series(spy_close).dropna().astype(float)
    except Exception:
        return unknown
    if len(s) < window + slope_lookback:
        return unknown
    dma = s.rolling(window).mean()
    if pd.isna(dma.iloc[-1]) or pd.isna(dma.iloc[-1 - slope_lookback]):
        return unknown
    return {"above_200dma": bool(s.iloc[-1] > dma.iloc[-1]),
            "rising_200dma": bool(dma.iloc[-1] > dma.iloc[-1 - slope_lookback])}


def _regime_read(regime, now, max_age_hours):
    """``{label, committed, direction, stale, age_hours}`` from the cached
    payload. Any malformed field degrades to unknown-and-stale."""
    out = {"label": None, "committed": None, "direction": 0,
           "stale": True, "age_hours": None}
    if not isinstance(regime, dict):
        return out
    label = regime.get("label")
    out["label"] = label if isinstance(label, str) else None
    committed = regime.get("committed_label")
    out["committed"] = committed if isinstance(committed, str) else None
    direction = regime.get("direction")
    out["direction"] = direction if isinstance(direction, int) else 0
    raw = regime.get("as_of") or regime.get("ts")
    try:
        as_of = dt.datetime.fromisoformat(str(raw))
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=dt.timezone.utc)
        age = (now - as_of).total_seconds() / 3600.0
        out["age_hours"] = age
        out["stale"] = age > max_age_hours
    except (TypeError, ValueError):
        pass
    return out


def _is_downward(read):
    """Has the app committed to a DOWNWARD direction, freshly?

    Both the word and the sign are checked because either can be absent, and a
    stale read counts for nothing regardless of what it says."""
    if read["stale"]:
        return False
    if (read["label"] or "").strip().lower() in _DOWNWARD_LABELS:
        return True
    return read["direction"] < 0


def _summary(trend, read):
    bits = []
    if trend["above_200dma"] is None:
        bits.append("SPY trend unknown")
    elif trend["above_200dma"]:
        bits.append("SPY above a %s 200-DMA"
                    % ("rising" if trend["rising_200dma"] else "flat/falling"))
    else:
        bits.append("SPY below its 200-DMA")
    if read["committed"]:
        bits.append(read["committed"].replace("_", " ").title())
    if read["label"]:
        bits.append(read["label"])
    if read["stale"]:
        bits.append("regime read is stale")
    return " · ".join(bits)


def direction_clearance(spy_close, regime, now=None,
                        max_age_hours=MAX_REGIME_AGE_HOURS):
    """What the tape permits, per side.

    Returns ``{market, long, short}``; each side carries a ``state`` and a
    non-empty ``reasons`` list. **Both sides are always present** — a blocked
    side WITH its reasons is a research finding, while a missing side is an
    absence the reader has to interpret."""
    now = now or dt.datetime.now(dt.timezone.utc)
    trend = spy_trend(spy_close)
    read = _regime_read(regime, now, max_age_hours)

    above = trend["above_200dma"]
    rising = trend["rising_200dma"]
    downward = _is_downward(read)

    # ── short ──────────────────────────────────────────────────────────────
    # The STRUCTURAL read outranks the fast one, and the horizons are why. The
    # 200-DMA is a multi-week structure; the committed direction comes from a
    # 5-minute EMA slope and a 15-minute composite. Letting an intraday
    # "Softening" clear a TWENTY-DAY directional short against a rising 200-DMA
    # is the same horizon mismatch the audit criticised in the legacy engine.
    #
    # Caught live rather than in review: SPY above a rising 200-DMA with the
    # regime reading Softening returned BOTH sides cleared — a contradiction on
    # its face. So a downward regime may tip a structure that has stopped
    # rising, and may not override one that has not.
    short_reasons = []
    if above is False:
        short_state = "cleared"
        short_reasons.append("SPY is below its 200-DMA")
    elif downward and rising is False:
        short_state = "cleared"
        short_reasons.append("SPY above a flat/falling 200-DMA")
        short_reasons.append("committed direction is %s"
                             % (read["label"] or "downward"))
    else:
        short_state = "relative_only"
        if above is None:
            short_reasons.append("SPY trend unknown — too little history")
        else:
            short_reasons.append(
                "SPY above a %s 200-DMA"
                % ("rising" if rising else "flat/falling"))
        if read["stale"]:
            short_reasons.append(
                "regime read is stale, so it cannot clear a directional short")
        elif read["label"]:
            short_reasons.append("committed direction is %s" % read["label"])

    # ── long ───────────────────────────────────────────────────────────────
    if above is True:
        long_state = "cleared"
        long_reasons = ["SPY above a %s 200-DMA"
                        % ("rising" if rising else "flat/falling")]
    elif above is False:
        long_state = "relative_only"
        long_reasons = ["SPY is below its 200-DMA"]
    else:
        long_state = "relative_only"
        long_reasons = ["SPY trend unknown — too little history"]

    return {
        "market": {
            "spy_above_200dma": above,
            "spy_200dma_rising": rising,
            "regime": read["committed"],
            "label": read["label"],
            "direction": read["direction"],
            "stale": read["stale"],
            "age_hours": read["age_hours"],
            "summary": _summary(trend, read),
        },
        "long": {"state": long_state, "reasons": long_reasons},
        "short": {"state": short_state, "reasons": short_reasons},
    }
