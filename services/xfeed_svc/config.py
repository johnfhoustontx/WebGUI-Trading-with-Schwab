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
    # What the PUBLIC site's X Feed (live.neuralstrike.co/x-feed) shows. The
    # service writes the two public views with only these accounts; the private
    # page is unaffected. "*" is every watched account; otherwise group names
    # (any case), with ``ungrouped`` adding the accounts in no group.
    "public": {
        "enabled": True,
        "groups": ["*"],
        "ungrouped": True,
    },
    # Live posts to a Discord channel as FxEmbed links (discord_feed.py). Ships OFF.
    # The webhook is NOT here: it is a secret, routes.x_feed.discord in
    # shared/notifications.json, and there is no fall back to another webhook.
    "discord": {
        "enabled": False,
        "groups": ["News"],
        "ungrouped": False,
        "types": ["post", "reply"],
        "link_host": "fixupx.com",
        "max_per_poll": 10,
        "send_gap_s": 2.0,
        "max_retries": 3,
        "timeout_s": 10,
        "queue_max": 200,
        "keep_days": 30,
    },
}

load, reset = toml_loader(XFEED_TOML, DEFAULTS, label="xfeed.toml")
