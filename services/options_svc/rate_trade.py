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


def _norm_symbol(s):
    """``$SPX`` and ``spx`` name the same underlying on the Calculator."""
    return str(s or "").strip().upper().lstrip("$")


def rate(symbol, structure, legs, cc, market_state=None):
    """``{"row", "error"}`` - one graded, stamped Strategy Finder row, or a
    sentence saying why there is none.

    ``cc`` is the ``cache:options:calc_chain`` payload the Calculator already
    loaded, so the rating fetches no chain. The row is scored WITHOUT the Finder's
    quality cut or the volatility gate's drop - a weak or cheap-premium trade still
    comes back graded, with ``vol_gate_blocks`` saying what the gate would have done.
    """
    if not isinstance(cc, dict) or not cc.get("chain"):
        return {"row": None, "error": "Load the chain first - there is nothing to rate against."}
    cc_symbol = cc.get("symbol")
    if _norm_symbol(cc_symbol) != _norm_symbol(symbol):
        return {"row": None, "error": (f"The loaded chain is for {cc_symbol}, not {symbol} "
                                        "- reload the chain and rate again.")}
    spot = _price(cc.get("price"))
    if spot is None:
        return {"row": None, "error": "The chain carries no underlying price - reload it."}
    finder, reason = finder_legs(legs, cc["chain"], spot)
    if finder is None:
        return {"row": None, "error": reason}
    try:
        return {"row": _score(symbol, structure, finder, legs, cc, spot, market_state),
                "error": None}
    except Exception as exc:  # noqa: BLE001 - a rating that raises still answers.
        _degrade.degraded("options.calc_rate", detail=symbol)
        return {"row": None,
                "error": f"The rating could not be computed ({type(exc).__name__})."}


def _score(symbol, structure, finder, page_legs, cc, spot, market_state):
    client = compute._proxy.schwab_py_client
    api = cc.get("api") or symbol
    hist = se.fetch_price_history(client, api)
    tech = se.calc_technicals(hist) if hist is not None else {}
    iv = run_iv_analysis(client, api, price=spot, hist=hist, chain=cc["chain"]) or {}
    dem, atm_iv = compute.scan_vol_inputs(iv, spot)
    view = ssc.infer_market_view(tech or {}, iv)

    net_delta = ssn.payoff_metrics(finder, spot, symbol).get("net_delta")
    stype, family, label, bias, known = structure_meta(structure, net_delta)
    row = ssn._assemble(stype, family, label, bias, finder, symbol, spot, atm_iv)
    em_1sd = (dem or 0.0) * math.sqrt(max(row.get("dte") or 0, 1))
    ssc.score_all([row], view, atm_iv, em_1sd, market_state=market_state, daily_move=dem)

    iv_rank = iv.get("iv_rank")
    row["vol_gate_blocks"] = bool(_vol_gate.signal_blocks(
        row, floor=_scanner_config.min_iv_rank().get(TRADE_TYPE),
        ceiling=_scanner_config.max_iv_rank().get(TRADE_TYPE), iv_rank=iv_rank))
    row["iv_rank"] = iv_rank
    row["daily_em"] = dem
    row["structure_known"] = known
    row["id"] = f"calc_rate_{symbol}"
    try:
        earnings = compute.scan_earnings(symbol)
    except Exception:  # noqa: BLE001 - costs the earnings line, not the rating.
        _degrade.degraded("options.calc_rate_earnings", detail=symbol)
        earnings = ("not_listed", None)
    compute.stamp_candidate(row, trade_type=TRADE_TYPE, earnings=earnings,
                            iv_rank_known=iv_rank is not None)
    return row
