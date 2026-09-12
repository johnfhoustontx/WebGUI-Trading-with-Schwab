"""Should this candidate be sold, or bought, at today's volatility?

One pure predicate, read by ``options-scanner/scanner_engine.py`` and
``services/options_svc/compute.py`` — two tiers that cannot import each other,
which is what makes this a shared module rather than a helper beside either
caller. It is **vocabulary over two readings and two bounds**; the bounds
themselves are policy and live in ``config/scanner.toml`` (``[iv_rank]`` for the
sell floors, ``[iv_rank_ceiling]`` for the buy ceilings), reached through
``shared.scanner_config``. Same split as ``shared/structures.py`` and
``config/trade_mgmt.toml``.

⚠ **It keys on the candidate's own VEGA SIGN, never on its structure name.**
Every surface that needs this emits a MIXED list — the Market Scanner's
Directional tab carries naked shorts beside long calls and puts, and the Strategy
Finder carries debit verticals beside credit spreads. A blanket floor over such a
list would refuse the long-premium candidates too, and cheap volatility is
exactly when those are the right trade: the gate would cut hardest where it
should not cut at all.

Design + the measurement behind the levels:
docs/plans/2026-09-12-volatility-gate-design.md. The short version is that the
floor is worth having — over 910 closed captured signals on prod, entry IV rank
below 45 measured **mean R −0.150 at a 25.5% win rate** while everything above
55 measured +0.22 to +0.27 — and that the effect survives a control for the
composite score, so it is a genuinely separate axis rather than the score in
disguise.
"""
import math

SHORT_PREMIUM = "short"
LONG_PREMIUM = "long"

IV_TOO_LOW = "IV_TOO_LOW"
IV_TOO_HIGH = "IV_TOO_HIGH"


def _finite(value):
    """A real number, or ``None``. Rejects ``bool`` and every non-finite float.

    The same contract as ``webgui/pages/fmt.num`` and
    ``shared.driver_policy``'s own coercion, and it is strict here for the
    documented reason: this value is about to reach a ``<`` comparison, and a NaN
    makes every comparison False — which switches a bound silently OFF rather
    than raising. ``bool`` is excluded because ``float(True)`` is 1.0 and would
    read as a vega of +1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def premium_side(net_vega):
    """``SHORT_PREMIUM`` / ``LONG_PREMIUM`` / ``None`` for an unreadable vega.

    ⚠ A vega of **exactly zero is neither side**, not "long". A vega-neutral
    structure has no stake in the volatility regime, and returning a side there
    would hand it a bound on the strength of a rounding sign — note that
    ``-0.0 < 0`` is False in Python, so the two spellings of zero would not even
    agree with each other.
    """
    v = _finite(net_vega)
    if v is None or v == 0:
        return None
    return LONG_PREMIUM if v > 0 else SHORT_PREMIUM


def blocks(iv_rank, net_vega, floor=None, ceiling=None):
    """``IV_TOO_LOW`` / ``IV_TOO_HIGH`` / ``None`` — may this candidate be traded?

    ``floor`` refuses SHORT premium below it (selling cheap volatility);
    ``ceiling`` refuses LONG premium above it (buying expensive volatility).
    Both are inclusive: a reading exactly at a bound passes, mirroring
    ``run_full_scan``'s existing ``>= min_rank``.

    Three absence rules, each of which is the actual content of this function:

    * **An unreadable input SKIPS the gate.** ``iv_analysis`` returns an
      ``iv_rank`` of ``None`` by design when a symbol has too little HV history,
      and a bound cannot be enforced against an unknown. A data outage must
      degrade to "ungated", never to "refused" — the inverse of this repo's
      documented NaN trap, where a missing reading pins a bound and an outage
      renders as a confident extreme.
    * **A bound of 0 (or absent) is OFF.** Not "a floor at zero": an IV rank of
      **0.0 is a real reading** — the live income board's top-ranked candidate
      carried 0.1 — so a floor of 0 must not be the thing that refuses it. This
      is what lets ``[iv_rank_ceiling]`` ship as all-zero and mean "no ceiling".
    * **A non-finite bound is treated as absent**, for the same reason ``_finite``
      exists: a NaN floor makes ``iv < floor`` False and switches the gate off
      while looking configured.
    """
    side = premium_side(net_vega)
    if side is None:
        return None
    iv = _finite(iv_rank)
    if iv is None:
        return None
    if side is SHORT_PREMIUM:
        lo = _finite(floor)
        if lo and iv < lo:
            return IV_TOO_LOW
        return None
    hi = _finite(ceiling)
    if hi and iv > hi:
        return IV_TOO_HIGH
    return None


def signal_blocks(signal, floor=None, ceiling=None, iv_rank=None):
    """:func:`blocks` over one candidate dict.

    ``iv_rank`` overrides the row's own value when supplied, because the Market
    Scanner stamps ``iv_rank`` onto its rows only AFTER its filters run — there
    the reading lives in ``results["iv_data"][symbol]`` and the row does not
    carry it yet. Explicit wins over the row so a caller holding the authoritative
    per-symbol reading cannot be overruled by a stale one copied onto a candidate.
    """
    signal = signal or {}
    reading = iv_rank if iv_rank is not None else signal.get("iv_rank")
    return blocks(reading, signal.get("net_vega"), floor=floor, ceiling=ceiling)
