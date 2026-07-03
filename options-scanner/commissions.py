"""
commissions.py - PURE Schwab commission helper for the swing scanner.

Reads the single source of truth ``config/commissions.toml`` (Schwab standard:
options $0.65/contract, applied PER LEG, on open AND close) from the repo root.
This is a scanner-local mirror of ``services/options_svc/commission.py`` — the
scanner MUST NOT import from ``services/`` (cross-app collision + layering), so
the toml is re-read here through the repo-root path.

Defensive: a missing toml / unparseable rate degrades to a safe default rather
than raising (a swing candidate should never crash on commission math).
"""
import pathlib

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from shared.symbols import INDEX_ROOTS as _INDEX_ROOTS, is_index_symbol  # noqa: E402

# Repo root = parent of options-scanner/. Kept path-derived (not imported from
# repo_paths) so this module has zero import-time coupling and can't fail import.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TOML_PATH = _REPO_ROOT / "config" / "commissions.toml"

# Safe defaults mirroring the toml (Schwab standard) if the file is unreadable.
_DEFAULT_EQUITY_RATE = 0.65
_DEFAULT_INDEX_RATE = 0.65
_DEFAULT_INDEX_FEE = 0.0


def _load_rates():
    try:
        with open(_TOML_PATH, "rb") as fh:
            return tomllib.load(fh)
    except Exception:  # pragma: no cover - defensive
        return {}


_RATES = _load_rates()


def _option_rate(symbol):
    opt = (_RATES.get("options") if isinstance(_RATES, dict) else None) or {}
    if is_index_symbol(symbol):
        try:
            return float(opt.get("index", _DEFAULT_INDEX_RATE)) + \
                float(opt.get("index_exchange_fee", _DEFAULT_INDEX_FEE))
        except (TypeError, ValueError):
            return _DEFAULT_INDEX_RATE + _DEFAULT_INDEX_FEE
    try:
        return float(opt.get("equity", _DEFAULT_EQUITY_RATE))
    except (TypeError, ValueError):
        return _DEFAULT_EQUITY_RATE


def round_trip_commission(legs, symbol, qty=1):
    """Round-trip (open + close) option commission in per-contract-position dollars.

    = per-leg rate x number of legs x qty x 2 (entry + exit). Index roots use the
    index rate (+ exchange passthrough) via ``is_index_symbol``.

    ``legs`` may be an int leg-count or a list/tuple of legs (its length is used).
    A non-positive leg-count or qty -> 0.0. Never raises.
    """
    try:
        n_legs = len(legs) if isinstance(legs, (list, tuple)) else int(legs)
        q = int(qty)
    except (TypeError, ValueError):
        return 0.0
    if n_legs <= 0 or q <= 0:
        return 0.0
    return round(n_legs * q * _option_rate(symbol) * 2, 4)
