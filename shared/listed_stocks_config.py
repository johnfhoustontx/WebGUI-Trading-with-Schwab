"""``config/listed_stocks.toml``: one set of defaults for the file's two readers.

``tools/rank_listed_stocks.py`` (the Saturday re-rank) reads ``[blend]``,
``[inputs]``, ``[fetch]`` and ``[safety]``; ``services/trade_svc/listed_stocks.py``
(the Trade Analyzer's lookup) reads ``[display]``. They share this loader so the
shipped file has ONE table of built-in defaults to match, rather than each
reader carrying the half it uses.

Read at call time by both: a change needs no restart.
"""
from repo_paths import LISTED_STOCKS_TOML
from shared.config_toml import toml_loader

DEFAULTS = {
    # How many of an industry's top-ranked stocks the Trade Analyzer lists.
    "display": {"industry_top": 5},
    "blend": {"dollar_volume_weight": 0.5},
    "inputs": {"market_cap": "schwab"},
    "fetch": {"fundamentals_batch": 100, "quotes_batch": 150, "pause_sec": 2.0},
    "safety": {"min_coverage": 0.90},
}
load_config, reset_config = toml_loader(LISTED_STOCKS_TOML, DEFAULTS,
                                        label="listed_stocks.toml")
