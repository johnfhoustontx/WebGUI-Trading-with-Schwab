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
    "iv_rank": {"0-DTE": 35, "SWING": 30},
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
    """``{"0-DTE": int, "SWING": int}`` - the shape scanner_engine expects."""
    sec = _section("iv_rank")
    return {k: sec.get(k, DEFAULTS["iv_rank"][k]) for k in DEFAULTS["iv_rank"]}


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
