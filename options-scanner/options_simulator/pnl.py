"""Taylor-series P&L decomposition.

Convention matches options_calculator's Greeks:
  - theta is per-day  -> multiply by (dt_years * 365) to recover per-step
  - vega  is per 1 vol point -> multiply dsigma by 100
"""


def decompose(greeks_t0, dS, dt_years, dsigma):
    delta = greeks_t0["delta"] * dS
    gamma = 0.5 * greeks_t0["gamma"] * dS ** 2
    theta = greeks_t0["theta"] * dt_years * 365.0
    vega  = greeks_t0["vega"]  * dsigma * 100.0
    rho   = 0.0
    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega":  vega,
        "rho":   rho,
        "total": delta + gamma + theta + vega + rho,
    }
