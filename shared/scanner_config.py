"""Scanner selection floors, from ``config/scanner.toml``.

These decide whether a signal fires at all - IV-rank minimums, per-VIX-regime
credit floors, the directional delta band, the score cutoffs. They are the most
retuned block in the scanner (the constants they replaced carried dated
"2026-06-11 quality retune" comments in the source), and they are the documented
reason index names rarely produce signals, so they are exactly what an operator
wants to experiment with without editing Python.

Read from three modules that cannot share imports directly:
``options-scanner/scanner_engine.py``, ``options-scanner/signal_recorder.py`` and
``services/options_svc/compute.py``.

Missing file / bad TOML / missing key -> the built-in defaults, never a raise.
"""
from repo_paths import SCANNER_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    # Selling floors. INCOME matches SWING deliberately - see config/scanner.toml.
    "iv_rank": {"0-DTE": 35, "SWING": 30, "INCOME": 30},
    # Buying ceilings, all OFF (0). Mechanism shipped, gate disabled: this app has
    # no long-premium outcome data to set a level from. See max_iv_rank().
    "iv_rank_ceiling": {"0-DTE": 0, "SWING": 0, "INCOME": 0},
    "credit": {
        "swing": 0.12,
        "zero_dte": {"LOW": 0.08, "NORMAL": 0.12, "ELEVATED": 0.15, "HIGH": 0.20},
    },
    "directional": {
        "min_credit_pct": 0.20,
        "max_risk_pct": 0.02,
        "max_per_symbol_bucket": 2,
        "pcs_delta": [-0.55, -0.30],
        "ccs_delta": [0.30, 0.55],
    },
    "single_leg": {
        "max_per_symbol": 8,
        "min_score": 50.0,
        "excluded_grades": ["Weak"],
    },
    "scores": {
        "capture_min": 58,
        # The Income Window's own capture floor, and it is 0 ON PURPOSE - see
        # the comment in config/scanner.toml. The whole board scores 50-57
        # against capture_min 58, so sharing that floor would record NOTHING and
        # the feature would be a green no-op.
        "capture_min_income": 0,
        "neg_gex_min": 62,
        "gex_strong_neg": -0.30,
        "swing_min": 50.0,
    },
}

load, reset_cache = toml_loader(SCANNER_TOML, DEFAULTS, label="scanner.toml")


def _section(name):
    sec = load().get(name)
    return sec if isinstance(sec, dict) else DEFAULTS[name]


def min_iv_rank() -> dict:
    """``{"0-DTE": int, "SWING": int, "INCOME": int}`` - selling floors.

    ⚠ **Closed over ``DEFAULTS["iv_rank"]``**, so a trade type added to the TOML
    alone is SILENTLY DROPPED. That is deliberate — it makes a typo'd key a
    no-op rather than a phantom floor — but it means adding a surface takes both
    halves. ``test_vol_gate.py`` pins it, because the failure mode is a scan that
    reads as gated and never gates: ``INCOME`` was exactly that until 2026-09-12,
    since ``income_scan`` passes ``trade_type="INCOME"`` and
    ``MIN_IV_RANK.get(trade_type, 0)`` answered 0.
    """
    sec = _section("iv_rank")
    return {k: sec.get(k, DEFAULTS["iv_rank"][k]) for k in DEFAULTS["iv_rank"]}


def max_iv_rank() -> dict:
    """Buying CEILINGS, per trade type - refuse LONG premium above this IV rank.

    The mirror of :func:`min_iv_rank`, and **0 means off** (see
    ``shared.vol_gate.blocks``), which is how every entry ships. There is no
    long-premium outcome data in this app — ``signals.db`` holds only PCS, CCS and
    IC — so a level here would be invention rather than measurement. The natural
    setting is 65, ``strategy_scoring.infer_market_view``'s own "high" boundary
    and the mirror of the floor's 35 being its "low" one; the TOML says so.

    Keyed on the same trade types as the floors on purpose: a type carried by one
    accessor and not the other is how one surface silently loses half the gate.
    """
    sec = _section("iv_rank_ceiling")
    return {k: sec.get(k, DEFAULTS["iv_rank_ceiling"][k])
            for k in DEFAULTS["iv_rank_ceiling"]}


def min_credit_pct() -> dict:
    """``{"0-DTE": {regime: pct}, "SWING": pct}``.

    Flattened from the TOML's ``[credit] swing`` + ``[credit.zero_dte]`` because
    a bare ``"0-DTE"`` key cannot hold a sub-table in TOML without quoting
    gymnastics, and the engine's existing shape is the one worth preserving.
    """
    sec = _section("credit")
    zero = sec.get("zero_dte")
    if not isinstance(zero, dict):
        zero = DEFAULTS["credit"]["zero_dte"]
    return {
        "0-DTE": {k: zero.get(k, DEFAULTS["credit"]["zero_dte"][k])
                  for k in DEFAULTS["credit"]["zero_dte"]},
        "SWING": sec.get("swing", DEFAULTS["credit"]["swing"]),
    }


def directional() -> dict:
    return _section("directional")


def directional_delta_range() -> dict:
    """``{"PCS": (lo, hi), "CCS": (lo, hi)}`` - tuples, as the engine expects."""
    d = directional()
    out = {}
    for key, cfg_key in (("PCS", "pcs_delta"), ("CCS", "ccs_delta")):
        try:
            lo, hi = d[cfg_key]
            out[key] = (float(lo), float(hi))
        except Exception:
            lo, hi = DEFAULTS["directional"][cfg_key]
            out[key] = (float(lo), float(hi))
    return out


def single_leg() -> dict:
    return _section("single_leg")


def scores() -> dict:
    return _section("scores")
