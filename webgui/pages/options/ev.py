"""Expected value for the Trade detail panel -- PURE builders.

Two rows that answer different questions:

* ``breakeven_facts`` -- **structural**. The win rate this trade's own price
  DEMANDS: ``max_loss / (credit + max_loss)``, equivalently ``1 - credit/width``.
  Derived from two figures the panel already shows, and immune to the
  delta-versus-mark mismatch that ruins the priced EV.
* ``calibrated_facts`` -- **the recommendation**. What signals in the same family
  and score band actually RETURNED, read from ``cache:options:calibration``.

⚠ **The priced EV is deliberately not here.** ``pop_pct`` against ``rr_pct`` is
~0 by construction -- both come from the option's own price -- and where it is
large it is measuring a broken mark: measured on prod 2026-08-25, the top three
live signals by priced EV carried relative bid-ask spreads of 225%, 239% and
395%. Showing it would rank the least trustworthy marks first. It stays where it
is already correct: a GATE in ``scanner_engine.select_best_width``, not a display.

⚠ **``max_profit`` is never used as ``b``.** For a long put it assumes the
underlying reaches zero while ``pop_pct`` is P(any profit); multiplying the two
prints **+2137R**. A signal carrying ``max_profit`` is refused outright. Note the
existing ``unbounded`` flag does NOT catch this -- it reads False for every one
of the worst offenders.

``shared.calibration`` is imported for ``bucket_key`` alone. It is pure math
(``import math`` and nothing else -- no engines, no sqlite, no Schwab), and
sharing it is what stops the two tiers spelling the family differently: the DB
says ``'0DTE'`` and the page says ``'0-DTE'``.
"""
from shared.calibration import bucket_key

from ..fmt import num
from .theme import TXT_NEG, TXT_NEUTRAL, TXT_POS, TXT_WARN

# Data-driven tone maps from a KNOWN FINITE SET to a static class (Tailwind-first
# rule): never an f-string arbitrary value built at runtime.
TONE_CLASSES = (TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL)

# Points of cushion over the required win rate before the margin reads as
# comfortable rather than marginal.
_COMFORTABLE_PP = 5.0


def _margin_tone(margin_pp):
    if margin_pp is None:
        return TXT_NEUTRAL
    if margin_pp >= _COMFORTABLE_PP:
        return TXT_POS
    return TXT_WARN if margin_pp >= 0 else TXT_NEG


def breakeven_facts(signal):
    """``{breakeven_pct, margin_pp, tone, text}`` or ``None``.

    ``None`` means the panel shows NOTHING -- not a dash, not a flagged number.
    That is consistent with how the panel already treats missing keys, and the
    repo's own history is the argument: a confident wrong number outlives its
    caveat.
    """
    s = signal or {}
    if s.get("max_profit") is not None:
        return None                      # tail-outcome shape; see the module doc
    credit, max_loss = num(s.get("credit")), num(s.get("max_loss"))
    if credit is None or max_loss is None or max_loss <= 0:
        return None
    total = credit + max_loss
    if total <= 0:
        return None

    breakeven_pct = max_loss / total * 100.0
    pop = num(s.get("pop_pct"))
    margin_pp = None if pop is None else pop - breakeven_pct
    return {"breakeven_pct": breakeven_pct, "margin_pp": margin_pp,
            "tone": _margin_tone(margin_pp),
            "text": f"needs {breakeven_pct:.1f}%"}


def calibrated_facts(signal, payload):
    """``{ev_r, n, days, tone, text}`` or ``None``.

    ``None`` whenever the bucket is absent, the cache is cold, or the bucket
    does not ``speak`` -- the service sets that flag when the day-clustered t is
    inside +/-2, because one scan emits a dozen correlated signals and an EV we
    cannot separate from zero is not a recommendation.
    """
    s = signal or {}
    buckets = (payload or {}).get("buckets") if isinstance(payload, dict) else None
    if not isinstance(buckets, dict):
        return None

    # ⚠ Reads `trade_type`/`composite_score` ONLY, deliberately. The panel is
    # shared and each table synthesizes its own signal-like dict, but every
    # synthesizer is responsible for supplying those two names — Captured
    # Signals stores `scanner_type`/`entry_score` and `synth_from_captured`
    # already maps them across. A `scanner_type`/`entry_score` fallback HERE was
    # written on 2026-08-25 and reverted the same hour: measured against the real
    # synthesized dicts it changed nothing (104 rows either way), and its tests
    # passed only because they fed a shape no producer emits. That is the
    # documented trap — a consumer-side guard proves nothing until a test drives
    # it from the PRODUCER, which is what test_ev.py now does instead.
    key = bucket_key(s.get("trade_type"), num(s.get("composite_score")))
    bucket = buckets.get(key) if key else None
    if not isinstance(bucket, dict) or not bucket.get("speaks"):
        return None

    ev_r, n, days = num(bucket.get("ev_r")), bucket.get("n"), bucket.get("days")
    if ev_r is None:
        return None

    tone = TXT_POS if ev_r > 0 else (TXT_NEG if ev_r < 0 else TXT_NEUTRAL)
    return {"ev_r": ev_r, "n": n, "days": days, "tone": tone,
            "text": f"{ev_r:+.2f}R per trade · {n} trades over {days} days"}
