"""Rate my trade - grade ONE hand-built Calculator trade the way the Strategy
Finder grades what it builds. Design: docs/plans/2026-09-16-calc-rate-my-trade-design.md.

Nothing here re-derives an economics figure or a score: the legs become a
Strategy Finder row (``strategy_scanner._assemble``), graded by
``strategy_scoring.score_all`` and stamped by ``compute.stamp_candidate``, exactly
as ``compute.swing_scan`` and the Finder handler do. What is new is only the
conversion from the Calculator's leg shape, and the two things a scan does that a
rating must NOT: the quality cut and the volatility drop, since a weak trade must
still come back graded.
"""
import datetime as _dt
import math

from services import _degrade
from services.options_svc import compute  # noqa: F401 - puts options-scanner on sys.path
from shared import scanner_config as _scanner_config
from shared import vol_gate as _vol_gate

import scanner_engine as se  # noqa: E402
import strategy_scanner as ssn  # noqa: E402
import strategy_scoring as ssc  # noqa: E402
from iv_analysis import run_iv_analysis  # noqa: E402

#: The scan every hand-built trade is judged as. The Strategy Finder's stamps,
#: floors and gates all key on it.
TRADE_TYPE = "SWING"

#: Calculator template code -> the Strategy Finder's (type, family, label, bias).
#: Read off the builders in ``strategy_scanner`` (``_DIRECTIONAL``,
#: ``build_debit_verticals``, ``build_straddles_strangles``,
#: ``build_butterflies_condors``, ``build_calendars``/``_diagonal``,
#: ``build_stock_structures``) and the credit adapters, so a rated trade carries the
#: same identity a scanned one would. ⚠ Every code in the Calculator's
#: ``STRATEGY_TEMPLATES`` must appear here - ``shared/tests/test_cross_tier_mirrors``
#: pins it, because neither tier can import the other.
CALC_TO_SCORER = {
    "LONG_CALL": ("LONG_CALL", "DIRECTIONAL", "Long Call", "bullish"),
    "LONG_PUT": ("LONG_PUT", "DIRECTIONAL", "Long Put", "bearish"),
    "NAKED_CALL": ("SHORT_CALL", "DIRECTIONAL", "Short Call", "bearish"),
    "NAKED_PUT": ("SHORT_PUT", "DIRECTIONAL", "Short Put", "bullish"),
    "PCS": ("PCS", "VERTICAL", "Put Credit Spread", "bullish"),
    "CCS": ("CCS", "VERTICAL", "Call Credit Spread", "bearish"),
    "VERT_CALL_DEBIT": ("BULL_CALL", "VERTICAL", "Bull Call Spread", "bullish"),
    "VERT_PUT_DEBIT": ("BEAR_PUT", "VERTICAL", "Bear Put Spread", "bearish"),
    "LONG_STRADDLE": ("LONG_STRADDLE", "VOLATILITY", "Long Straddle", "neutral"),
    "SHORT_STRADDLE": ("SHORT_STRADDLE", "NEUTRAL", "Short Straddle", "neutral"),
    "LONG_STRANGLE": ("LONG_STRANGLE", "VOLATILITY", "Long Strangle", "neutral"),
    "SHORT_STRANGLE": ("SHORT_STRANGLE", "NEUTRAL", "Short Strangle", "neutral"),
    # The Finder's adapted iron condor keeps the scanner's "IC" type.
    "IC": ("IC", "NEUTRAL", "Iron Condor", "neutral"),
    "CONDOR_CALL": ("CONDOR_CALL", "NEUTRAL", "Call Condor", "neutral"),
    "CONDOR_PUT": ("CONDOR_PUT", "NEUTRAL", "Put Condor", "neutral"),
    "BUTTERFLY_CALL": ("BUTTERFLY_CALL", "NEUTRAL", "Call Butterfly", "neutral"),
    "BUTTERFLY_PUT": ("BUTTERFLY_PUT", "NEUTRAL", "Put Butterfly", "neutral"),
    "IRON_BUTTERFLY": ("IRON_BUTTERFLY", "NEUTRAL", "Iron Butterfly", "neutral"),
    "CALENDAR_CALL": ("CALENDAR_CALL", "NEUTRAL", "Call Calendar", "neutral"),
    "CALENDAR_PUT": ("CALENDAR_PUT", "NEUTRAL", "Put Calendar", "neutral"),
    "DIAGONAL_CALL": ("DIAGONAL_CALL", "DIRECTIONAL", "Call Diagonal", "bullish"),
    "DIAGONAL_PUT": ("DIAGONAL_PUT", "DIRECTIONAL", "Put Diagonal", "bearish"),
    "COVERED_CALL": ("COVERED_CALL", "DIRECTIONAL", "Covered Call", "bullish"),
    "PROTECTIVE_PUT": ("PROTECTIVE_PUT", "DIRECTIONAL", "Protective Put", "bullish"),
    "COLLAR": ("COLLAR", "DIRECTIONAL", "Collar", "bullish"),
}

#: |net delta| inside this reads as neutral for a structure no template names.
_BIAS_DEADBAND = 0.05

_OPTION_KINDS = ("call", "put")


def structure_meta(code, net_delta):
    """``(type, family, label, bias, known)`` for a Calculator shape code.

    A code the map does not know - ``"CUSTOM"``, None, anything else - keeps type
    ``CUSTOM``, which the scorer grades against its DEBIT bars, and ``known``
    False so the dialog can say so. Its bias is the sign of its net delta."""
    meta = CALC_TO_SCORER.get(code)
    if meta:
        return (*meta, True)
    d = _finite(net_delta)
    if d is None or abs(d) < _BIAS_DEADBAND:
        bias = "neutral"
    else:
        bias = "bullish" if d > 0 else "bearish"
    return ("CUSTOM", "CUSTOM", "Custom structure", bias, False)


def _finite(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _price(v):
    """A usable page price (> 0), else None."""
    p = _finite(v)
    return p if p is not None and p > 0 else None


def _expiry_label(expiry):
    try:
        d = _dt.date.fromisoformat(str(expiry))
    except ValueError:
        return str(expiry)
    return f"{d:%b} {d.day}"


def _ratio(qtys):
    """Every quantity divided by the smallest, when all divide evenly - so a
    10-lot and a 1-lot rate the same. Otherwise unchanged."""
    q = [max(int(x or 1), 1) for x in qtys]
    lo = min(q)
    if all(x % lo == 0 for x in q):
        return [x // lo for x in q]
    return q


def finder_legs(legs, chain, spot):
    """``(finder_legs, None)`` or ``(None, reason)``.

    Quotes, Greeks, IV, open interest and volume come from ``chain`` (the
    Calculator's cached ``calc_chain``); the ``mark`` is the page's own price when
    it has a usable one - the operator is rating the trade at the price they
    entered - else the chain's. A share leg is one 100-share lot at the page's
    price, else spot."""
    legs = [l for l in (legs or []) if isinstance(l, dict)]
    if not legs:
        return None, "Build a trade first - there are no legs to rate."
    by_kind = {}
    out = []
    for leg in legs:
        kind = str(leg.get("option_type") or "").lower()
        side = "short" if leg.get("side") == "short" else "long"
        if kind == ssn._oc.STOCK_KIND:
            stock = ssn._stock_leg(_price(leg.get("premium")) or spot)
            stock["side"] = side
            out.append(stock)
            continue
        strike = _finite(leg.get("strike"))
        expiry = leg.get("expiry")
        if kind not in _OPTION_KINDS or strike is None or not expiry:
            return None, "Every leg needs a strike and an expiration before it can be rated."
        if kind not in by_kind:
            by_kind[kind] = ssn.extract_options(chain or {}, kind, 0, 100000)
        strikes = (by_kind[kind].get(str(expiry)) or {}).get("strikes") or {}
        data = next((v for k, v in strikes.items() if abs(k - strike) < 1e-6), None)
        if data is None:
            return None, (f"No quote for the {strike:g} {kind} expiring "
                          f"{_expiry_label(expiry)} - reload the chain.")
        finder = ssn._leg_from(data, kind, side, str(expiry))
        page_price = _price(leg.get("premium"))
        if page_price is not None:
            finder["mark"] = page_price
        out.append(finder)
    for finder, qty in zip(out, _ratio([l.get("qty") for l in legs])):
        finder["qty"] = qty
    return out, None
