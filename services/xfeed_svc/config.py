"""config/xfeed.toml - every tunable of the X accounts feed.

Read through ``shared.config_toml.toml_loader`` (built-in defaults <- the tracked
file <- config/local/xfeed.toml), mtime-cached, so the poller can call ``load()``
every tick and pick up a saved change with no restart. Treat the result as
read-only: it is the cached mapping.
"""
from repo_paths import REPO_ROOT
from shared.config_toml import toml_loader

XFEED_TOML = REPO_ROOT / "config" / "xfeed.toml"

DEFAULTS = {
    "fxembed": {
        "base_url": "https://api.fxtwitter.com",
        "user_agent": "neuralstrike-xfeed/1.0",
        "timeout_s": 15,
        "not_found_retries": 3,
        "retry_delay_s": 0.5,
    },
    "poller": {
        "interval_s": 60,          # minimum 30, enforced in code
        "account_gap_s": 1.0,
        "page_gap_s": 1.0,
        "max_pages": 25,
        "backoff_s": [60, 120, 300],
        "not_found_limit": 3,
        "live_slack_min": 15,
        "recent_factor": 3,
        "tick_s": 5,
    },
    "views": {
        "latest_rows": 300,
        "query_limit_max": 1000,
        "top_tickers": 10,
        "answer_ttl_s": 120,
        "group_name_max": 30,
    },
    "page": {
        "new_highlight_s": 60,
        "answer_poll_s": 0.25,
        "answer_timeout_s": 20,
    },
}

load, reset = toml_loader(XFEED_TOML, DEFAULTS, label="xfeed.toml")
