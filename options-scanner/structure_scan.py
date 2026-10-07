"""Structures other than credit spreads for the Market Scanner's two windows.

PURE. Chains, a price and a market view in; scored candidates out. No Schwab
call, no store. ``scanner_engine.run_full_scan`` calls this in a guarded block,
the way it calls the single-leg pass, and puts the result in its OWN lists
(``structures_0dte`` / ``structures_swing``): ten readers of the credit lists
assume the credit shape, and the two scores are not comparable.

The builders are the Strategy Finder's (``strategy_scanner``) and the scoring
call is the Finder's too, so a candidate is the same row with the same score on
both surfaces. What differs is the window: the Finder keeps straddles,
strangles, butterflies and condors at least seven days out, and the Scanner
hands those builders its own window minimum (operator decision 2026-10-06).
Ratio backspreads (the RATIO family) take the nearest expiry on both surfaces.

Design and the measurement behind the defaults:
docs/plans/2026-10-06-scanner-multi-structure-design.md.
"""
import logging
import math
import pathlib as _pathlib
import sys as _sys

import strategy_scanner as _ssn
import strategy_scoring as _ssc

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import vol_gate as _vol_gate  # noqa: E402

log = logging.getLogger("structure_scan")


def merge_chains(front, back):
    """One chain holding both chains' expirations, for a calendar's two months.

    The front chain's own fields win - the underlying price among them, and its
    copy of an expiration both chains list. Neither input is mutated; an
    unusable ``back`` returns ``front`` itself.
    """
    if not back or back.get("status") == "FAILED":
        return front
    out = dict(front)
    for key in ("callExpDateMap", "putExpDateMap"):
        merged = dict(back.get(key) or {})
        merged.update(front.get(key) or {})
        out[key] = merged
    return out


def _tag(batch, group):
    """Stamp ``group`` - the BUILD family - on each candidate. Distinct from the
    scoring ``family`` a row already carries: a long straddle is built by
    STRADDLE and scored as VOLATILITY. The page groups by what built a row."""
    batch = list(batch)
    for s in batch:
        s["group"] = group
    return batch


def build_window(chain, symbol, spot, atm_iv, dte_min, dte_max, *, families,
                 short_band, back_chain=None, back_dte_max=None,
                 max_debit_frac=None):
    """Every candidate one DTE window offers, unscored.

    ``families`` is the set of build groups to run (``config/scanner.toml``
    ``[structures] families``); a name this function does not know builds
    nothing. ``short_band`` is ``(lo, hi)`` ABSOLUTE deltas for a short
    strangle's legs: it aims at the midpoint and the ceiling binds.

    ``back_chain`` / ``back_dte_max`` supply a calendar's later month. Without
    them no calendar or diagonal is built, which is how the 0-DTE window is
    called: a calendar's front leg sits at least a week out (measured - every
    shorter one was cut on reward), so it belongs to the Swing window alone.
    Only the calendar builder sees the merged chain; every other structure is
    built on the window's own expiries.

    ``max_debit_frac`` caps what a ratio backspread may cost, as a fraction of
    its strike distance (``[structures] backspread_max_debit_frac``); ``None``
    leaves the builder's own default.
    """
    fams = set(families or ())
    lo, hi = short_band
    out = []
    if "VERTICAL" in fams:
        out += _tag(_ssn.build_debit_verticals(chain, symbol, spot, atm_iv,
                                               dte_min, dte_max), "VERTICAL")
    if "STRADDLE" in fams:
        out += _tag(_ssn.build_straddles_strangles(
            chain, symbol, spot, atm_iv, dte_min, dte_max,
            put_band=(-hi, -lo), call_band=(lo, hi),
            min_front_dte=dte_min), "STRADDLE")
    if "BUTTERFLY" in fams:
        out += _tag(_ssn.build_butterflies_condors(
            chain, symbol, spot, atm_iv, dte_min, dte_max,
            min_front_dte=dte_min), "BUTTERFLY")
    if "RATIO" in fams:
        out += _tag(_ssn.build_backspreads(chain, symbol, spot, atm_iv, dte_min,
                                           dte_max, max_debit_frac=max_debit_frac),
                    "RATIO")
    if "CALENDAR" in fams and back_chain is not None and back_dte_max:
        out += _tag(_ssn.build_calendars(merge_chains(chain, back_chain), symbol,
                                         spot, atm_iv, dte_min, back_dte_max),
                    "CALENDAR")
    return out


def _add(bucket, key, n):
    if bucket is not None and n:
        bucket[key] = bucket.get(key, 0) + n


def select(candidates, *, view, atm_iv, daily_em, dte_min, iv_rank, floor,
           ceiling, spans_earnings, earnings_date, keep_long_through_earnings,
           min_score, excluded_grades, max_per_family, bucket=None):
    """Score one window's candidates and apply the Scanner's gates, in order:
    volatility, earnings, quality, cap. Returns the survivors, best first.

    ``bucket`` (optional) receives ``built`` and one counter per stage, each
    ADDED to what is there, so that over one call::

        built == vol_gate + earnings + score_cut + capped + len(result)

    Counting never moves a decision: with ``bucket=None`` the result is the same.

    **Volatility** is ``shared.vol_gate.signal_blocks`` on the candidate's own
    vega sign: ``floor`` refuses short premium below it, ``ceiling`` long premium
    above it, and an unknown ``iv_rank`` skips the gate.

    **Earnings** asks ``spans_earnings(candidate)`` and decides on the same vega
    sign: long premium is kept and stamped ``spans_earnings`` / ``earnings_date``
    when ``keep_long_through_earnings``; short premium, and a row whose vega
    cannot be read, is dropped - an unreadable row never earns the exemption.

    **The cap is per FAMILY** (``group``), the best ``max_per_family`` of each;
    0 is no cap. Scores sit in bands by family (measured: debit spreads 73-78,
    long straddles 50-56), so one cap across families would drop long
    volatility every time.
    """
    # The same scoring call the single-leg pass and the Strategy Finder make.
    # ``em_1sd`` (the move at the window's minimum) is only the fallback for a
    # daily move the scorer cannot use.
    em_1sd = (daily_em or 0.0) * math.sqrt(max(dte_min, 1))
    scored = _ssc.score_all(candidates, view, atm_iv, em_1sd, daily_move=daily_em)
    if bucket is not None:
        bucket["built"] = bucket.get("built", 0) + len(scored)

    kept = [s for s in scored
            if not _vol_gate.signal_blocks(s, floor=floor, ceiling=ceiling,
                                           iv_rank=iv_rank)]
    _add(bucket, "vol_gate", len(scored) - len(kept))

    through = []
    for s in kept:
        if spans_earnings(s):
            long_premium = (_vol_gate.premium_side(s.get("net_vega"))
                            is _vol_gate.LONG_PREMIUM)
            if not (keep_long_through_earnings and long_premium):
                continue
            s["spans_earnings"], s["earnings_date"] = True, earnings_date
        through.append(s)
    _add(bucket, "earnings", len(kept) - len(through))

    passing = [s for s in through
               if (s.get("composite_score") or 0) >= min_score
               and s.get("grade") not in excluded_grades]
    _add(bucket, "score_cut", len(through) - len(passing))

    passing.sort(key=lambda s: (s.get("composite_score") or 0), reverse=True)
    out, taken = [], {}
    for s in passing:
        group = s.get("group")
        if max_per_family and taken.get(group, 0) >= max_per_family:
            continue
        taken[group] = taken.get(group, 0) + 1
        out.append(s)
    _add(bucket, "capped", len(passing) - len(out))
    return out
