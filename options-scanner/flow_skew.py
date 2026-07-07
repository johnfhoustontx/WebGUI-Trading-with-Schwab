"""
flow_skew.py - PURE options-flow skew helpers for the market-state classifier.

Derives two "options-flow" signals from a raw Schwab ``get_option_chain`` payload:

  1. ``risk_reversal_25d`` - the 25-delta risk-reversal (put IV - call IV) at the
     FRONT (nearest) expiration. Positive = downside fear (bearish); a steepening
     call skew (more negative) = bullish.
  2. ``index_call_put_volume`` - total call vs put ``totalVolume`` across the whole
     chain, plus the put/call ratio (higher = more put activity).

PURE ENGINE MODULE: standard-library + math only. NO ``services/`` import, NO
proxy, NO I/O. Mirrors the pure-engine discipline of ``commissions.py`` so it can
be lazily imported by a service later and still import cleanly standalone.

IV UNITS: ``volatility`` is consumed in its native Schwab convention (PERCENT,
e.g. 18.5 = 18.5%) and returned unchanged - callers get IVs in PERCENT.

All functions are fully defensive: they never raise on missing keys, None values,
or malformed contracts - they return ``None`` (or skip the bad contract) instead.

Version: 1.0.0
Last Updated: 2026-07-07

Version 1.0.0 Changes:
- Initial implementation (25-delta risk-reversal + index call/put volume).
"""

#############################################
# CONSTANTS
#############################################

_CALL_MAP = "callExpDateMap"
_PUT_MAP = "putExpDateMap"

_CALL_TARGET_DELTA = 0.25
_PUT_TARGET_DELTA = -0.25


#############################################
# INTERNAL HELPERS
#############################################

def _exp_date(exp_key):
    """The date portion of a ``"<expiration>:<dte>"`` map key (str, sortable)."""
    return str(exp_key).split(":", 1)[0]


def _shared_front_keys(call_map, put_map):
    """(call_key, put_key) for the earliest expiration DATE present in BOTH maps.

    Reading both 25-delta legs from the SAME expiration makes the tenor
    correct-by-construction (no mismatched-tenor path from a one-sided front
    series). Returns (None, None) when either map is missing/empty or the two
    maps share no common expiration date.
    """
    if not isinstance(call_map, dict) or not isinstance(put_map, dict):
        return None, None
    if not call_map or not put_map:
        return None, None
    # Map each expiration DATE -> its full key, per side (first key wins on a
    # duplicate date, which the Schwab shape does not produce).
    call_by_date = {}
    for k in call_map:
        call_by_date.setdefault(_exp_date(k), k)
    put_by_date = {}
    for k in put_map:
        put_by_date.setdefault(_exp_date(k), k)
    shared = set(call_by_date) & set(put_by_date)
    if not shared:
        return None, None
    front = min(shared)
    return call_by_date[front], put_by_date[front]


def _iter_contracts(strike_map):
    """Yield every well-formed contract dict in a ``{strike: [contract, ...]}`` map."""
    if not isinstance(strike_map, dict):
        return
    for contracts in strike_map.values():
        if not isinstance(contracts, (list, tuple)):
            continue
        for c in contracts:
            if isinstance(c, dict):
                yield c


def _as_float(val):
    """Coerce to float, or None if missing/non-numeric."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _pick_nearest_delta(strike_map, target):
    """The IV (native percent) of the contract whose delta is nearest ``target``.

    Only contracts with BOTH a non-None delta and a non-None volatility are
    considered. Returns None if the map has no such usable contract.
    """
    best_iv = None
    best_dist = None
    for c in _iter_contracts(strike_map):
        delta = _as_float(c.get("delta"))
        iv = _as_float(c.get("volatility"))
        if delta is None or iv is None:
            continue
        dist = abs(delta - target)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_iv = iv
    return best_iv


def _sum_volume(exp_map):
    """Total ``totalVolume`` across every contract in an expiration map."""
    total = 0
    if not isinstance(exp_map, dict):
        return total
    for strike_map in exp_map.values():
        for c in _iter_contracts(strike_map):
            vol = _as_float(c.get("totalVolume"))
            if vol is not None:
                total += vol
    return int(total)


#############################################
# PUBLIC API
#############################################

def risk_reversal_25d(chain):
    """25-delta risk-reversal at the shared FRONT expiration: ``put_iv - call_iv``.

    Uses ONE shared front expiration = the earliest expiration DATE present in
    BOTH ``callExpDateMap`` and ``putExpDateMap`` (so both legs come from the same
    tenor, correct-by-construction — a one-sided front series can't mix tenors),
    then finds the CALL nearest to +0.25 delta and the PUT nearest to -0.25 delta
    in that expiration, reading each one's ``volatility``.

    Returns ``{"put_iv", "call_iv", "rr"}`` (IVs in the chain's native PERCENT;
    ``rr`` positive = downside fear), or ``None`` when the chain is empty / has no
    maps / there is no expiration common to both maps / the shared front lacks a
    usable call or put (a usable contract has both a non-None delta and a non-None
    volatility). Never raises.
    """
    if not isinstance(chain, dict):
        return None

    call_map = chain.get(_CALL_MAP)
    put_map = chain.get(_PUT_MAP)

    call_key, put_key = _shared_front_keys(call_map, put_map)
    if call_key is None or put_key is None:
        return None

    call_iv = _pick_nearest_delta(call_map.get(call_key), _CALL_TARGET_DELTA)
    put_iv = _pick_nearest_delta(put_map.get(put_key), _PUT_TARGET_DELTA)
    if call_iv is None or put_iv is None:
        return None

    return {"put_iv": put_iv, "call_iv": call_iv, "rr": put_iv - call_iv}


def index_call_put_volume(chain):
    """Total call vs put ``totalVolume`` across the whole chain + put/call ratio.

    Sums ``totalVolume`` over ALL call contracts and ALL put contracts (every
    expiration in the maps). Returns ``{"call_vol", "put_vol", "ratio"}`` where
    ``ratio = put_vol / call_vol`` (higher = more put activity), with ``ratio``
    set to ``None`` when ``call_vol == 0`` (div-by-zero guard). An empty chain
    (no maps at all) returns ``None``. Never raises.
    """
    if not isinstance(chain, dict):
        return None

    call_map = chain.get(_CALL_MAP)
    put_map = chain.get(_PUT_MAP)
    if not isinstance(call_map, dict) and not isinstance(put_map, dict):
        return None

    call_vol = _sum_volume(call_map)
    put_vol = _sum_volume(put_map)
    ratio = None if call_vol == 0 else put_vol / call_vol

    return {"call_vol": call_vol, "put_vol": put_vol, "ratio": ratio}
