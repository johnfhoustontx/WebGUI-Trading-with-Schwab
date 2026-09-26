"""config/news.toml — the news collector's feeds, cadence and ticker extras.

Tier-2 (news_svc) reads it; Tier 1 reads only ``trending.window_h`` and the
ticker set. Stdlib + shared.config_toml + shared.symbols, nothing else —
``shared/tests/test_news_config.py`` pins that import set, because the webgui
imports this module and it therefore sits on the Tier-1 allow-list.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
Treat anything ``load()`` returns as read-only: it is the cached mapping.
"""
import copy
import logging

from repo_paths import REPO_ROOT
from shared import symbols as _symbols
from shared.config_toml import toml_loader
from shared.symbols import clean_symbol

log = logging.getLogger(__name__)

NEWS_TOML = REPO_ROOT / "config" / "news.toml"

KINDS = ("rss", "yahoo_ticker", "google_news", "edgar_form4", "edgar_filings")

DEFAULTS = {
    "collector": {
        "rth_poll_min": 5, "offhours_poll_min": 15, "weekend_poll_min": 60,
        "keep_days": 7, "view_items": 300, "request_timeout_s": 20,
        "sec_user_agent": "NeuralStrike news_svc contact@neuralstrike.co",
    },
    "tickers": {"extras": []},
    "trending": {"window_h": 6},
    "feeds": [],
}

load, reset_cache = toml_loader(NEWS_TOML, DEFAULTS, label="news.toml")


def feeds() -> list:
    """Enabled feeds with ``enabled`` / ``public`` filled in; unknown kinds dropped.

    Each feed is a deep COPY - ``load()`` returns the cached mapping, so filling
    the flags in place would write into every later reader's config."""
    out = []
    for raw in load().get("feeds") or []:
        if not isinstance(raw, dict):
            continue
        feed = copy.deepcopy(raw)
        feed.setdefault("enabled", True)
        feed.setdefault("public", True)
        if feed.get("kind") not in KINDS:
            log.warning("news.toml: feed %r has unknown kind %r - skipped",
                        feed.get("name"), feed.get("kind"))
            continue
        if not feed.get("name") or not feed["enabled"]:
            continue
        out.append(feed)
    return out


def ticker_set() -> list:
    """The GEX collection list plus ``[tickers] extras``, cleaned, order kept."""
    extras = (load().get("tickers") or {}).get("extras") or []
    out, seen = [], set()
    for raw in list(_symbols.collection_base()) + list(extras):
        sym = clean_symbol(raw)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out
