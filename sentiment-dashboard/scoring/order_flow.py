"""Aggressor order-flow classifier (quote rule + tick test, CVD) — pure.

Pure functions: quote/trade scalars in, JSON-safe dicts/ints/floats out (no
pandas, no tk, no I/O). One input to the five-state classifier's *aggression*
axis — fed a live stream of trades, it labels each trade buyer- or
seller-initiated (Lee-Ready: the quote rule, breaking mid-spread ties with the
tick test), then aggregates a window into an aggressor ratio + cumulative volume
delta (CVD). Positive = net buying = motivated buying, ALIGNED with the
aggression axis (no sign flip). Mirrors the _clamp / defensive / confidence-in-
[0,1] idiom of scoring/effort.py and scoring/aggression.py.

Documented choices:
- Mid-spread tie-break (tick test): equal-to-prev OR no prior trade -> 0
  (indeterminate). CVD/ratio treat a 0-sign tick as neutral (no volume booked to
  either side) rather than guessing a direction.
- Missing size -> 1.0 (count-weighted): a trade with no reported size still
  happened; treating it as one unit keeps the ratio meaningful. A size that is
  present but malformed (non-numeric) -> 0.0 (skip its volume; the tick is still
  counted in n).
"""
from __future__ import annotations

import re
from ._common import clamp as _clamp, num as _num

MIN_TICKS_FULL = 30    # tick count at which flow confidence saturates

# OCC/OSI option symbol tail: a 6-digit YYMMDD date, the type char (C/P), then an
# 8-digit strike (strike×1000, zero-padded). Anchored at end so the space-padded
# root (e.g. "SPY   ") is ignored. Schwab OSIs look like "SPY   260717C00500000".
_OSI_TAIL_RE = re.compile(r"\d{6}([CP])\d{8}$")


def classify_tick(last, bid, ask, prev_last=None) -> int:
    """Aggressor sign of one trade: +1 buyer-initiated, -1 seller-initiated,
    0 indeterminate. Quote rule first (last>=ask lifts the offer, last<=bid hits
    the bid); strictly between -> tick test vs prev_last. Never raises."""
    p = _num(last)
    if p is None:
        return 0
    a = _num(ask)
    b = _num(bid)
    if a is not None and p >= a:
        return 1
    if b is not None and p <= b:
        return -1
    # strictly inside the spread (or quotes missing) -> tick test
    pv = _num(prev_last)
    if pv is None:
        return 0
    if p > pv:
        return 1
    if p < pv:
        return -1
    return 0


def aggregate_flow(ticks) -> dict:
    """Reduce a list of ticks (oldest first) to window flow stats.

    Each tick: {last, size, bid, ask}. prev_last is carried across the sequence
    internally for the tick test. Returns
    {buy_vol, sell_vol, cvd, aggressor_ratio, n} — aggressor_ratio is None when
    total volume is 0. Defensive: empty -> zeros; never raises on malformed ticks.
    """
    buy_vol = 0.0
    sell_vol = 0.0
    n = 0
    prev_last = None
    if not ticks:
        return {"buy_vol": 0.0, "sell_vol": 0.0, "cvd": 0.0,
                "aggressor_ratio": None, "n": 0}
    for t in ticks:
        if not isinstance(t, dict):
            continue
        n += 1
        sign = classify_tick(t.get("last"), t.get("bid"), t.get("ask"),
                             prev_last=prev_last)
        last = _num(t.get("last"))
        if last is not None:
            prev_last = last
        raw_size = t.get("size")
        size = 1.0 if raw_size is None else _num(raw_size)
        if size is None:
            size = 0.0
        if sign > 0:
            buy_vol += size
        elif sign < 0:
            sell_vol += size
    total = buy_vol + sell_vol
    ratio = None if total <= 0 else _clamp((buy_vol - sell_vol) / total, -1.0, 1.0)
    return {
        "buy_vol": round(buy_vol, 4),
        "sell_vol": round(sell_vol, 4),
        "cvd": round(buy_vol - sell_vol, 4),
        "aggressor_ratio": None if ratio is None else round(ratio, 4),
        "n": n,
    }


def flow_aggression_component(agg) -> tuple:
    """Map an aggregate_flow result (or any dict carrying aggressor_ratio + n)
    to a signed aggression component + confidence. Positive score = net buying =
    motivated buying (aligned with the aggression axis, NO flip). Confidence
    scales with n, saturating at MIN_TICKS_FULL. ratio None / n<=0 -> (0.0, 0.0).
    Never raises."""
    if not isinstance(agg, dict):
        return 0.0, 0.0
    ratio = _num(agg.get("aggressor_ratio"))
    n = _num(agg.get("n")) or 0.0
    if ratio is None or n <= 0:
        return 0.0, 0.0
    score = _clamp(ratio, -1.0, 1.0)
    conf = _clamp(n / MIN_TICKS_FULL, 0.0, 1.0)
    return round(score, 4), round(conf, 4)


# ── OPTION aggressor flow (put/call pressure) ────────────────────────────────
def osi_option_type(osi) -> str | None:
    """'C' or 'P' — the option-type char that follows the 6-digit YYMMDD date in
    an OCC/OSI symbol (e.g. ``"SPY   260717C00500000"`` -> 'C'), else None.

    Defensive about the space-padded root + case; a non-string or a symbol that
    doesn't match the OSI tail (date+type+strike) returns None. Never raises."""
    if not isinstance(osi, str):
        return None
    m = _OSI_TAIL_RE.search(osi.strip().upper())
    return m.group(1) if m else None


def aggregate_option_flow(ticks) -> dict:
    """Reduce a list of near-ATM option ticks (oldest first) to put/call pressure.

    Each tick: {osi, last, size, bid, ask}. prev_last is carried PER OSI (a
    ``{osi: prev_last}`` map) for the tick test. Classify each via ``classify_tick``,
    tag call/put via ``osi_option_type`` (an untaggable OSI is skipped), and
    accumulate the four aggressor volumes. Missing size -> 1.0 (count-weighted),
    malformed size -> 0.0 (as the equity path).

    Returns ``{call_buy, call_sell, put_buy, put_sell, n, signal}`` where
    ``signal`` = the signed [-1,1] normalized
    ``((call_buy - call_sell) - (put_buy - put_sell)) / total`` (positive = net
    call-buying / put-selling = BULLISH; negative = put-buying = BEARISH), or None
    when total option volume is 0. Defensive: empty / all-untaggable -> signal None.
    """
    call_buy = call_sell = put_buy = put_sell = 0.0
    n = 0
    prev: dict = {}
    if not ticks:
        return {"call_buy": 0.0, "call_sell": 0.0, "put_buy": 0.0,
                "put_sell": 0.0, "n": 0, "signal": None}
    for t in ticks:
        if not isinstance(t, dict):
            continue
        osi = t.get("osi")
        typ = osi_option_type(osi)
        if typ is None:            # can't tag call/put -> not an option observation
            continue
        n += 1
        sign = classify_tick(t.get("last"), t.get("bid"), t.get("ask"),
                             prev_last=prev.get(osi))
        last = _num(t.get("last"))
        if last is not None:
            prev[osi] = last
        raw_size = t.get("size")
        size = 1.0 if raw_size is None else _num(raw_size)
        if size is None:
            size = 0.0
        if typ == "C":
            if sign > 0:
                call_buy += size
            elif sign < 0:
                call_sell += size
        else:  # 'P'
            if sign > 0:
                put_buy += size
            elif sign < 0:
                put_sell += size
    total = call_buy + call_sell + put_buy + put_sell
    if total <= 0:
        signal = None
    else:
        raw = (call_buy - call_sell) - (put_buy - put_sell)
        signal = _clamp(raw / total, -1.0, 1.0)
    return {
        "call_buy": round(call_buy, 4),
        "call_sell": round(call_sell, 4),
        "put_buy": round(put_buy, 4),
        "put_sell": round(put_sell, 4),
        "n": n,
        "signal": None if signal is None else round(signal, 4),
    }


def option_flow_component(agg) -> tuple:
    """Map an ``aggregate_option_flow`` result to a signed aggression component +
    confidence. Positive score = net call-buying / put-selling = BULLISH, ALIGNED
    with the aggression axis (NO flip — positive already = bullish). Confidence
    scales with n, saturating at MIN_TICKS_FULL. signal None / n<=0 -> (0.0, 0.0).
    Never raises."""
    if not isinstance(agg, dict):
        return 0.0, 0.0
    signal = _num(agg.get("signal"))
    n = _num(agg.get("n")) or 0.0
    if signal is None or n <= 0:
        return 0.0, 0.0
    score = _clamp(signal, -1.0, 1.0)
    conf = _clamp(n / MIN_TICKS_FULL, 0.0, 1.0)
    return round(score, 4), round(conf, 4)
