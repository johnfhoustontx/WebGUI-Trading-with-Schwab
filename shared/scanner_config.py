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
import math

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
        # A BOUGHT call or put is listed only when graded Good or Strong.
        "long_excluded_grades": ["Weak", "Marginal"],
    },
    # Structures other than credit spreads on the Market Scanner's two tabs
    # (options-scanner/structure_scan.py). See config/scanner.toml [structures].
    "structures": {
        "enabled": True,
        "families": ["VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR", "RATIO"],
        "min_score": 50.0,
        "excluded_grades": ["Weak"],
        "max_per_family": 2,
        "short_delta_min": 0.15,
        "backspread_max_debit_frac": 0.25,
        "earnings_long_premium": "flag",
    },
    "scores": {
        "capture_min": 58,
        # The Income Window's own capture floor, and it is 0 ON PURPOSE - see
        # the comment in config/scanner.toml. The whole board scores 50-57
        # against capture_min 58, so sharing that floor would record NOTHING and
        # the feature would be a green no-op.
        "capture_min_income": 0,
        # The tracked structures' own floor, 0 for the same reason: the scan's
        # quality cut ([structures] min_score) already decided what was shown.
        "capture_min_tracked": 0,
        "neg_gex_min": 62,
        "gex_strong_neg": -0.30,
        "swing_min": 50.0,
    },
    # At most this many OPEN captured signals per symbol, counted across every
    # scanner type. 0 = off. See config/scanner.toml [capture].
    "capture": {"max_open_per_symbol": 2,
                # Tracked structures (recorded to be measured, never traded):
                # whether they are recorded at all, and their OWN per-symbol pool.
                "tracked": True,
                # Per symbol WITHIN one family, and per family in all.
                "max_open_per_symbol_tracked": 1,
                "max_open_per_family_tracked": 10},
    # Which strikes the scanner may sell. Literals in scanner_engine.py until
    # 2026-10-04 (audit CQ-10). See config/scanner.toml [selection].
    "selection": {
        "max_entry_short_delta": 0.27,
        "momentum_veto": 0.6,
        "edge_margin": 0.02,
        "delta_sanity_max": 0.40,
        "min_abs_credit": 0.25,
        "min_abs_spread": 0.02,
        "max_width_dollars": 200,
        "zero_dte_min_mult": 0.618,
        "zero_dte_max_mult": 3.0,
        "directional_min_mult": 0.0,
        "directional_max_mult": 0.618,
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


def single_leg_long_excluded_grades() -> list:
    """Grades at which a BOUGHT call or put is never listed on the Directional
    tab. Shipped as Weak and Marginal, so only Good and Strong are shown. A
    value that is not a list of names is the shipped one; an empty list means
    the tab's general ``excluded_grades`` is the only cut. A fresh list."""
    default = DEFAULTS["single_leg"]["long_excluded_grades"]
    v = single_leg().get("long_excluded_grades", default)
    if not isinstance(v, list) or not all(isinstance(g, str) for g in v):
        return list(default)
    return [g for g in v if g.strip()]


STRUCTURE_FAMILIES = ("VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR", "RATIO")
EARNINGS_MODES = ("flag", "drop")


def structures() -> dict:
    """The Market Scanner's non-credit structures pass, every key of
    ``DEFAULTS["structures"]``.

    Closed over the defaults, like :func:`selection`: a key the code does not
    know is dropped and a value it cannot use is the shipped one, so a typo can
    never read as a gate that is on and never gates. Returns a fresh dict (and
    fresh lists) on every call - the loader's mapping is the cached one.

    ``families`` keeps only names in :data:`STRUCTURE_FAMILIES`.
    ``earnings_long_premium`` is ``"flag"`` (keep a long-premium trade that
    would be held through a report, and mark it) or ``"drop"``.
    """
    d = DEFAULTS["structures"]
    sec = load().get("structures")
    sec = sec if isinstance(sec, dict) else {}
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in d.items()}
    if isinstance(sec.get("enabled"), bool):
        out["enabled"] = sec["enabled"]
    fams = sec.get("families")
    if isinstance(fams, list):
        out["families"] = [f for f in (str(x).strip().upper() for x in fams)
                           if f in STRUCTURE_FAMILIES]
    grades = sec.get("excluded_grades")
    if isinstance(grades, list):
        out["excluded_grades"] = [str(g).strip().capitalize() for g in grades
                                  if str(g).strip()]
    for key in ("min_score", "short_delta_min", "backspread_max_debit_frac"):
        v = sec.get(key)
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v >= 0):
            out[key] = float(v)
    v = sec.get("max_per_family")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        out["max_per_family"] = v
    if sec.get("earnings_long_premium") in EARNINGS_MODES:
        out["earnings_long_premium"] = sec["earnings_long_premium"]
    return out


def scores() -> dict:
    return _section("scores")


def capture_max_open_per_symbol() -> int:
    """How many OPEN captured signals one symbol may hold, across all scanner
    types; ``0`` turns the cap off. A missing, negative or non-integer value
    falls back to the default rather than to "off" — a typo in a risk limit must
    not silently remove it."""
    default = DEFAULTS["capture"]["max_open_per_symbol"]
    v = _section("capture").get("max_open_per_symbol", default)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return default
    return v


def capture_tracked_enabled() -> bool:
    """Whether the Market Scanner's structures that are not credit spreads are
    recorded for study. Anything but a real boolean is the shipped ``True``."""
    v = _section("capture").get("tracked", DEFAULTS["capture"]["tracked"])
    return v if isinstance(v, bool) else DEFAULTS["capture"]["tracked"]


def capture_max_open_per_symbol_tracked() -> int:
    """How many OPEN tracked structures one symbol may hold IN ONE FAMILY (debit
    spreads, straddles and strangles, butterflies and condors, calendars, ratio
    spreads, single options), across both scan windows; ``0`` turns the cap off.

    Per family because the families score in bands: counted per symbol alone,
    the two best rows are almost always single options and a straddle is never
    recorded. Its OWN pool: never counted against
    :func:`capture_max_open_per_symbol`, whose slots the paper Account trades
    from. A missing, negative or non-integer value is the default."""
    default = DEFAULTS["capture"]["max_open_per_symbol_tracked"]
    v = _section("capture").get("max_open_per_symbol_tracked", default)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return default
    return v


def capture_max_open_per_family_tracked() -> int:
    """How many OPEN tracked structures one FAMILY may hold across every symbol;
    ``0`` turns the cap off. This is what bounds the cost: every open row is
    priced every 15 minutes, one chain per symbol and expiration. A missing,
    negative or non-integer value is the default."""
    default = DEFAULTS["capture"]["max_open_per_family_tracked"]
    v = _section("capture").get("max_open_per_family_tracked", default)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return default
    return v


def selection() -> dict:
    """The strike-selection thresholds, every key of ``DEFAULTS["selection"]``.

    Closed over the defaults, like :func:`min_iv_rank`: a key the code does not
    know is dropped (a typo is a no-op, never a phantom threshold), and a value
    that is not a real, finite number at or above zero is the shipped one. A
    threshold of NaN would make every comparison against it False, which reads
    as a gate that is on and never gates."""
    sec = load().get("selection")
    if not isinstance(sec, dict):
        sec = {}
    out = {}
    for key, default in DEFAULTS["selection"].items():
        value = sec.get(key, default)
        usable = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value) and value >= 0)
        out[key] = value if usable else default
    return out
