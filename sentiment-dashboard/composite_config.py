"""The live composite's input checks, from ``config/sentiment.toml``.

* ``min_breadth_issues`` — fewest NYSE issues (advancing + declining) that
  count as a live advance / decline reading.
* ``vix_max_skew_min`` — how much older than $VIX's print a $VIX1D or $VIX9D
  print may be and still be paired with it.

Missing file, bad TOML, or a missing or unusable value -> the built-in default,
never a raise. Read per call: the loader is mtime-cached, so an edit applies on
the next refresh with no restart.
"""
from repo_paths import SENTIMENT_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    "breadth": {"min_issues": 500},
    "vix": {"max_skew_min": 30},
}

load, reset_cache = toml_loader(SENTIMENT_TOML, DEFAULTS, label="sentiment.toml")


def _count(section, key):
    """``[section].key`` as a whole number >= 1, else the shipped default."""
    sec = load().get(section)
    raw = sec.get(key) if isinstance(sec, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return DEFAULTS[section][key]
    return raw


def min_breadth_issues() -> int:
    return _count("breadth", "min_issues")


def vix_max_skew_min() -> int:
    return _count("vix", "max_skew_min")
