"""Trade-management stop/target rules, from ``config/trade_mgmt.toml``.

These are the trader's own risk rules - when to take profit, when to cut, how far
a short delta may drift - so they belong in a file, not in two Python modules.

**They were mirrored BY HAND across folders that cannot import each other.**
``services/options_svc/rescue.py`` opened with a comment reading "Mirror
signal_recommender stop constants so detection stays consistent with the
auto-close manage cycle", and then restated four of them:

    rescue delta_critical      0.45  <-  DELTA_HARD_CEILING
    rescue delta_drift         0.12  <-  DELTA_DRIFT
    rescue dte_urgent          2     <-  CUT_DTE
    rescue money_tested_mult   2.0   <-  STOP_MULT

A silent disagreement there means the at-risk board flags a position the manage
cycle will not act on, or worse, stays quiet about one it will close. So
``rescue_thresholds()`` here DERIVES those four from ``[stops]`` rather than
listing them again - the mirror is now structural instead of clerical, and the
escalation bands rescue owns alone (warn levels, proximity, dte_manage) stay in
their own section.

Missing file / bad TOML / missing key -> the built-in defaults, never a raise.
"""
from repo_paths import TRADE_MGMT_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "stops": {
        # >= 50% of the credit captured -> ARM the break-even stop. Note this
        # arms a stop; it is not an immediate close.
        "tp_frac": 0.50,
        "stop_mult": 2.0,              # cut at >= 2x credit loss
        "delta_drift": 0.12,           # cut when short delta drifts this far past entry
        "delta_hard_ceiling": 0.45,    # ...but never hold past this, whatever the entry
        "delta_abs_fallback": 0.35,    # absolute breach when entry delta is unknown
        "cut_dte": 2,                  # cut when DTE <= this and underwater
        "recovery_dte_min": 5,         # min DTE to DEFER a soft delta stop
        "recovery_min_cushion": 0.015,  # min spot<->short-strike cushion to defer
    },
    "trail": {
        # Peak-driven profit-lock ladder for the armed break-even stop. Each rung
        # is [peak_frac, lock_frac]: once PEAK profit reaches peak_frac of the
        # credit, the stop ratchets to lock in lock_frac of it.
        #
        # The default is a single break-even rung (lock 0.0) - i.e. exactly the
        # plain break-even stop - so the ratchet is INERT until a caller passes a
        # richer ladder plus peak_pnl_frac in ctx.
        "default_ladder": [[0.50, 0.0]],
        "ratchet_ladder": [[0.50, 0.0], [0.65, 0.25], [0.80, 0.50]],
    },
    "rescue": {
        # Escalation bands the rescue board owns on its own. The four values it
        # SHARES with [stops] are not repeated here - see rescue_thresholds().
        "delta_warn": 0.30,
        "money_warn_mult": 1.0,          # x entry credit (loss)
        "money_critical_mult": 3.0,
        "dte_manage": 21,
        "proximity_watch_pct": 0.03,     # underlying within 3% of the short strike
        "proximity_tested_pct": 0.01,
    },
}

load, reset_cache = toml_loader(TRADE_MGMT_TOML, DEFAULTS, label="trade_mgmt.toml")


def _section(name):
    sec = load().get(name)
    return sec if isinstance(sec, dict) else DEFAULTS[name]


def stops() -> dict:
    return _section("stops")


def _ladder(key):
    """A TOML array-of-arrays -> the list[tuple] the recommender expects. A
    malformed rung is dropped rather than crashing the manage cycle."""
    raw = _section("trail").get(key) or DEFAULTS["trail"][key]
    out = []
    for rung in raw:
        try:
            peak, lock = rung
            out.append((float(peak), float(lock)))
        except Exception:
            continue
    return out or [(float(r[0]), float(r[1])) for r in DEFAULTS["trail"][key]]


def default_trail_ladder():
    return _ladder("default_ladder")


def ratchet_trail_ladder():
    return _ladder("ratchet_ladder")


def rescue_thresholds() -> dict:
    """The rescue board's escalation map.

    The four values shared with the manage cycle are READ FROM ``[stops]``, so
    the two can no longer drift; the rest are rescue's own.
    """
    st, rs = stops(), _section("rescue")
    return {
        "delta_warn": rs["delta_warn"],
        "delta_critical": st["delta_hard_ceiling"],   # <- shared
        "delta_drift": st["delta_drift"],             # <- shared
        "money_warn_mult": rs["money_warn_mult"],
        "money_tested_mult": st["stop_mult"],         # <- shared
        "money_critical_mult": rs["money_critical_mult"],
        "dte_manage": rs["dte_manage"],
        "dte_urgent": st["cut_dte"],                  # <- shared
        "proximity_watch_pct": rs["proximity_watch_pct"],
        "proximity_tested_pct": rs["proximity_tested_pct"],
    }
