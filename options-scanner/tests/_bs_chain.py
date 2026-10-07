"""A Schwab-shaped Black-Scholes chain for builder tests.

Mirrors ``tools/sweep_strategy_gates.chain``, which these tests cannot import.
Expirations are dated from TODAY, because ``strategy_scanner._dte_for`` reads the
real date: a fixed expiry string would drift into the past.
"""
import datetime as dt

import options_calculator as oc


def bs_chain(spot=100.0, iv=0.28, days=(2,), step=1.0, span=0.30):
    """One expiration per entry in ``days``, strikes ``spot`` +/- ``span`` on a
    ``step`` ladder, marks and greeks Black-Scholes at ``iv``."""
    r = oc.RISK_FREE_RATE
    out = {"underlyingPrice": spot, "callExpDateMap": {}, "putExpDateMap": {}}
    n = int(spot * span / step)
    for d in days:
        key = f"{(dt.date.today() + dt.timedelta(days=d)).isoformat()}:{d}"
        T = max(d, 0.5) / 365.0
        for kind, m in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            side = {}
            for i in range(-n, n + 1):
                K = round(spot + i * step, 2)
                mark = round(max(oc.bs_price(spot, K, T, r, iv, kind), 0.01), 2)
                side[f"{K}"] = [{
                    "delta": round(oc.bs_delta(spot, K, T, r, iv, kind), 4),
                    "mark": mark,
                    "bid": round(mark * 0.98, 2), "ask": round(mark * 1.02, 2),
                    "theta": round(float(oc.bs_theta(spot, K, T, r, iv, kind)), 4),
                    "vega": round(float(oc.bs_vega(spot, K, T, r, iv, kind)), 4),
                    "gamma": round(float(oc.bs_gamma(spot, K, T, r, iv, kind)), 5),
                    "volatility": iv * 100,
                    "totalVolume": 500, "openInterest": 2000}]
            out[m][key] = side
    return out
