"""The momentum cascade's tunables, from ``config/momentum.toml``.

* ``min_basket_members`` — how many admitted symbols a GICS sub-industry needs
  before it is scored as an equal-weight basket.
* ``quote_batch`` — how many symbols the Bull / Bear Map's live layer asks
  Schwab for in one ``/quotes`` call.

Missing file, bad TOML, or a missing or unusable value -> the built-in default,
never a raise.
"""
from repo_paths import MOMENTUM_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "subindustry": {"min_members": 2},
    "bullbear": {"quote_batch": 375},
}

load, reset_cache = toml_loader(MOMENTUM_TOML, DEFAULTS, label="momentum.toml")


def _count(section, key):
    """``[section].key`` as a whole number >= 1, else the shipped default."""
    sec = load().get(section)
    raw = sec.get(key) if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return DEFAULTS[section][key]
    return raw


def min_basket_members() -> int:
    return _count("subindustry", "min_members")


def quote_batch() -> int:
    return _count("bullbear", "quote_batch")
